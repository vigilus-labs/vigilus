"""Cron scheduler for recurring orchestrator tasks.

Each ScheduledTask holds a cron expression and a prompt. When a task fires,
the engine creates a fresh chat Session and runs the prompt through the
same orchestrator loop the chat UI uses, so the run shows up on the /chat
page with full delegation history, and results are persisted to the task
row for the Tasks page.

Lifecycle: ``get_scheduler().start()`` in the app lifespan startup loads all
enabled tasks; API mutations call ``sync_task``/``remove_task`` to keep the
running scheduler in step with the DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from vigilus.core.orchestrator import get_app_timezone
from vigilus.db.base import get_session_factory
from vigilus.db.models import ScheduledTask, Session

logger = structlog.get_logger(__name__)


def validate_cron(expression: str) -> str | None:
    """Return an error message if *expression* is not valid 5-field cron, else None."""
    try:
        CronTrigger.from_crontab(expression)
        return None
    except (ValueError, TypeError) as e:
        return str(e)


def next_fire_time(expression: str, tz: ZoneInfo | None = None) -> datetime | None:
    """Compute the next fire time for a cron expression in *tz* (app tz by default)."""
    tz = tz or get_app_timezone()
    try:
        trigger = CronTrigger.from_crontab(expression, timezone=tz)
        return trigger.get_next_fire_time(None, datetime.now(tz))
    except (ValueError, TypeError):
        return None


def _as_utc(value: datetime) -> datetime:
    """Treat naive timestamps (SQLite round-trips) as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def missed_fire(
    task: ScheduledTask, *, now: datetime | None = None, tz: ZoneInfo | None = None
) -> bool:
    """Whether at least one scheduled fire for *task* was missed (e.g. the
    backend was down at fire time). A fire only counts as missed when it falls
    after the task's last activity — its last run, or its creation for tasks
    that never ran — so a restart never replays work that already happened,
    and at most one catch-up run is owed no matter how many fires were skipped.
    """
    tz = tz or get_app_timezone()
    try:
        trigger = CronTrigger.from_crontab(task.cron_expression, timezone=tz)
    except (ValueError, TypeError):
        return False

    now = _as_utc(now) if now is not None else datetime.now(UTC)
    anchor = task.last_run_at or task.created_at
    if anchor is None:
        return False  # never ran and creation time unknown — don't guess
    anchor = _as_utc(anchor)
    if anchor >= now:
        return False

    # First fire strictly after the last activity; missed iff it already
    # came due. Works with APScheduler 3's get_next_fire_time: given a
    # "previous" time it returns the first fire after that instant.
    next_after_anchor = trigger.get_next_fire_time(anchor, now)
    return next_after_anchor is not None and next_after_anchor <= now


async def recover_stale_running_tasks() -> int:
    """Reset ScheduledTask rows left 'running' by a crash or restart.

    Without this, a task that died mid-run stays 'running' forever and the
    manual "Run now" button refuses with 409. Returns how many rows were
    reset. Called once during startup, before the scheduler loads tasks.
    """
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as db:
        stale = (
            (await db.execute(select(ScheduledTask).where(ScheduledTask.last_status == "running")))
            .scalars()
            .all()
        )
        for task in stale:
            task.last_status = "error"
            task.last_result = {
                **(task.last_result or {}),
                "status": "error",
                "error": "Interrupted — backend restarted mid-run",
            }
        if stale:
            await db.commit()
            logger.info(
                "scheduler.recovered_stale_tasks",
                count=len(stale),
                names=[t.name for t in stale],
            )
        return len(stale)


async def _deliver_to_channel(deliver_to: dict | None, summary: str, *, name: str) -> None:
    """Push a scheduled task summary to a channel chat via the gateway.

    ``deliver_to`` is ``{"platform": "telegram|discord", "chat_id": "..."}``.
    Best-effort: failures are logged but never fail the task run.
    """
    if not deliver_to:
        return
    platform = deliver_to.get("platform")
    chat_id = deliver_to.get("chat_id")
    if not platform or not chat_id:
        return
    try:
        from vigilus.integrations.gateway import get_gateway

        text = f"⏰ *{name}*\n\n{summary or '(no summary)'}"
        await get_gateway().send(platform, str(chat_id), text)
        logger.info("scheduler.delivered", task=name, platform=platform)
    except Exception as e:  # noqa: BLE001
        logger.warning("scheduler.delivery_failed", task=name, error=str(e))


async def execute_scheduled_task(task_id: str, *, force: bool = False) -> dict:
    """Run one scheduled task through the orchestrator. Returns the result dict.

    ``force=True`` (manual "Run now") executes even when the task is disabled.
    """
    # Imported here to avoid a circular import at module load
    # (core.turn imports api.chat, which imports core modules).
    from vigilus.core.events import get_event_bus
    from vigilus.core.orchestrator import OrchestratorNotConfigured

    factory = get_session_factory()
    event_bus = get_event_bus()
    started_at = datetime.now(UTC)

    async with factory() as db:
        task = await db.get(ScheduledTask, task_id)
        if not task:
            logger.warning("scheduler.task_missing", task_id=task_id)
            return {"status": "error", "error": "Task no longer exists"}
        if not task.enabled and not force:
            logger.info("scheduler.task_disabled", task_id=task_id, name=task.name)
            return {"status": "skipped", "error": "Task is disabled"}

        task.last_status = "running"
        task.last_run_at = started_at
        await db.commit()

        logger.info("scheduler.task_start", name=task.name, task_id=task.id)
        await event_bus.publish(
            "action.created",
            {
                "event_type": "action.created",
                "action": "scheduled_task_start",
                "task": task.name,
            },
        )

        # Wire the run to the same live-activity plumbing the chat page uses, so
        # a scheduled run can be watched and reviewed on /chat (under the Tasks
        # tab), and any JIT request it raises is forwarded into the session
        # stream as well as the global banner.
        from vigilus.api.sse import (
            EVT_DELEGATION_RESULT,
            EVT_DELEGATION_START,
            EVT_DONE,
            EVT_ERROR,
            EVT_JIT_REQUEST,
            EVT_TEXT_DELTA,
            EVT_THINKING,
            EVT_TOOL_CALL,
            EVT_TOOL_RESULT,
            StreamBridge,
            register_bridge,
            unregister_bridge,
        )
        from vigilus.core.tasks import get_task_registry

        activity_events = {
            EVT_THINKING,
            EVT_DELEGATION_START,
            EVT_TOOL_CALL,
            EVT_TOOL_RESULT,
            EVT_DELEGATION_RESULT,
            EVT_TEXT_DELTA,
            EVT_ERROR,
        }

        result: dict
        chat_session_id: str | None = None
        bridge: StreamBridge | None = None
        running_task = None
        forward_jit = None
        try:
            # Fresh chat session per run so the user can review the full
            # delegation transcript on the /chat page.
            chat_session = Session(
                title=f"⏰ {task.name} — {started_at.strftime('%Y-%m-%d %H:%M')}",
                origin="schedule",
            )
            db.add(chat_session)
            await db.commit()
            await db.refresh(chat_session)
            chat_session_id = chat_session.id

            prompt_text = task.task_prompt
            if task.operator_id:
                from vigilus.db.models import Operator

                hint_op = await db.get(Operator, task.operator_id)
                if hint_op:
                    prompt_text += (
                        f"\n\n(Scheduler hint: this task is usually handled by the "
                        f"'{hint_op.name}' operator.)"
                    )

            framed = (
                f"[SCHEDULED TASK: {task.name}] This message was sent "
                f"automatically by the task scheduler, not typed by the user. "
                f"Complete the task and produce a final report.\n\n{prompt_text}"
            )

            # Register the run so its activity is buffered and a client opening
            # the session mid-run can restore + follow it live.
            running_task = get_task_registry().register(chat_session.id, chat_session.title)

            def _record_activity(event: str, data: dict) -> None:
                if event in activity_events:
                    get_task_registry().record(chat_session.id, event, data)

            bridge = StreamBridge(on_event=_record_activity)
            register_bridge(chat_session.id, bridge)

            async def _forward_jit(payload: dict) -> None:
                bridge.publish(EVT_JIT_REQUEST, payload or {})

            forward_jit = _forward_jit
            event_bus.subscribe("jit.requested", forward_jit)

            from vigilus.core.turn import run_turn

            final_text = await run_turn(
                db,
                chat_session,
                framed,
                auto_title=False,
                bridge=bridge,
                cancel_event=running_task.cancel_event,
                unattended=True,
            )

            result = {
                "status": "success",
                "summary": final_text[:2000],
                "session_id": chat_session.id,
            }
        except OrchestratorNotConfigured as e:
            result = {"status": "error", "error": str(e), "session_id": chat_session_id}
        except Exception as e:
            logger.exception("scheduler.task_failed", name=task.name, error=str(e))
            result = {"status": "error", "error": str(e), "session_id": chat_session_id}
        finally:
            if forward_jit is not None:
                event_bus.unsubscribe("jit.requested", forward_jit)
            if chat_session_id is not None:
                get_task_registry().unregister(
                    chat_session_id, running_task.id if running_task is not None else None
                )
                if bridge is not None:
                    # Resolve any live SSE viewer's stream cleanly before close.
                    bridge.publish(EVT_DONE, {"session_id": chat_session_id})
                    bridge.close()
                unregister_bridge(chat_session_id)

        # Persist the outcome on the task row (re-fetch: session state may be stale)
        task = await db.get(ScheduledTask, task_id)
        if task:
            task.last_status = result["status"]
            task.last_result = result
            task.run_count = (task.run_count or 0) + 1
            task.next_run_at = next_fire_time(task.cron_expression) if task.enabled else None
            await db.commit()

            # Optional channel delivery: push the summary to a Telegram/Discord chat.
            if result["status"] == "success" and task.deliver_to:
                await _deliver_to_channel(
                    task.deliver_to, result.get("summary", ""), name=task.name
                )

        await event_bus.publish(
            "action.completed",
            {
                "event_type": "action.completed",
                "action": "scheduled_task_complete",
                "task": task.name if task else task_id,
                "status": result["status"],
            },
        )

        logger.info(
            "scheduler.task_done",
            task_id=task_id,
            status=result["status"],
            session_id=result.get("session_id"),
        )
        return result


class SchedulerEngine:
    """Wraps APScheduler; keeps cron jobs in sync with ScheduledTask rows."""

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    async def start(self) -> None:
        """Start the scheduler and register all enabled tasks from the DB."""
        if self.running:
            return
        self._scheduler = AsyncIOScheduler(timezone=get_app_timezone())
        self._scheduler.start()
        await self._load_enabled_tasks()

    async def _load_enabled_tasks(self) -> None:
        """Register every enabled task, recompute its next-run time, and catch
        up (once, coalesced) on any fire missed while the backend was down."""
        import asyncio

        from sqlalchemy import select

        tz = get_app_timezone()
        factory = get_session_factory()
        async with factory() as db:
            tasks = (
                (
                    await db.execute(
                        select(ScheduledTask).where(ScheduledTask.enabled == True)  # noqa: E712
                    )
                )
                .scalars()
                .all()
            )
            owed_catch_up: list[ScheduledTask] = []
            for task in tasks:
                self._register(task)
                task.next_run_at = next_fire_time(task.cron_expression, tz)
                if missed_fire(task, tz=tz):
                    owed_catch_up.append(task)
            await db.commit()

        if owed_catch_up:
            # Run each missed task once, regardless of how many fires were
            # skipped (a cron that fires every 5 minutes doesn't owe 2,016
            # runs after a week of downtime). These go through the exact same
            # execute path as scheduled fires, so results land on the Tasks
            # page like any other run.
            logger.info(
                "scheduler.catch_up",
                tasks=[t.name for t in owed_catch_up],
            )
            for task in owed_catch_up:
                asyncio.create_task(execute_scheduled_task(task.id))

        logger.info("scheduler.loaded", task_count=len(tasks), timezone=str(tz))

    async def reschedule_all(self) -> None:
        """Re-register all enabled tasks (e.g. after the app timezone changes)."""
        if not self.running:
            return
        assert self._scheduler is not None
        # Each job carries its own trigger timezone, so re-registering with
        # fresh CronTriggers is enough — no need to reconfigure the running
        # scheduler's default tz (which would raise while it's running).
        for job in self._scheduler.get_jobs():
            job.remove()
        await self._load_enabled_tasks()

    async def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("scheduler.stopped")

    def _register(self, task: ScheduledTask) -> None:
        """Add or replace the cron job for a task."""
        assert self._scheduler is not None
        self._scheduler.add_job(
            execute_scheduled_task,
            CronTrigger.from_crontab(task.cron_expression, timezone=get_app_timezone()),
            args=[task.id],
            id=task.id,
            name=task.name,
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1,
        )

    def sync_task(self, task: ScheduledTask) -> None:
        """Reflect a created/updated task in the running scheduler."""
        if not self.running:
            return
        if task.enabled:
            self._register(task)
        else:
            self.remove_task(task.id)

    def remove_task(self, task_id: str) -> None:
        if not self.running:
            return
        assert self._scheduler is not None
        job = self._scheduler.get_job(task_id)
        if job:
            job.remove()


_engine: SchedulerEngine | None = None


def get_scheduler() -> SchedulerEngine:
    global _engine
    if _engine is None:
        _engine = SchedulerEngine()
    return _engine
