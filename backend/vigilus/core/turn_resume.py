"""Resume a parked turn when its JIT request is resolved.

Approvals from the web API, a channel button, or a revoke all publish
``jit.resolved``. The subscriber starts ``resume_turn`` as a background task
so the request that resolved the grant does not keep the coroutine.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy import select, update

from vigilus.core.events import EventType, get_event_bus
from vigilus.core.orchestrator_loop import format_delegation_result, frame_delegation_result
from vigilus.core.tasks import get_task_registry
from vigilus.core.turn_park import TurnParkContext, TurnParked, load_messages
from vigilus.db.models import JitRequest, JitStatus, Message, MessageRole, Turn, TurnStatus
from vigilus.providers.base import LLMMessage

logger = structlog.get_logger(__name__)

_installed = False
_pending: list[asyncio.Task] = []


def spawn_resume(turn_id: str, resolved_status: str) -> asyncio.Task:
    """Run ``resume_turn`` in the background and keep the task reachable."""
    task = asyncio.create_task(resume_turn(turn_id, resolved_status))
    _pending.append(task)

    def _drop(done: asyncio.Task) -> None:
        if done in _pending:
            _pending.remove(done)
        if not done.cancelled() and done.exception() is not None:
            logger.error("turn.resume_task_failed", turn_id=turn_id, error=str(done.exception()))

    task.add_done_callback(_drop)
    return task


async def drain_resumes() -> None:
    """Wait for resumes spawned so far. Tests use this to avoid a race."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


def install_resume_subscriber() -> None:
    """Subscribe once to ``jit.resolved`` and resume the matching parked turn."""
    global _installed
    if _installed:
        return
    get_event_bus().subscribe(EventType.JIT_RESOLVED, _on_jit_resolved)
    _installed = True


async def _on_jit_resolved(payload: dict[str, Any]) -> None:
    request_id = (payload or {}).get("id")
    status = (payload or {}).get("status") or "denied"
    if not request_id:
        return
    from vigilus.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        turn_id = (
            (
                await db.execute(
                    select(Turn.id).where(
                        Turn.jit_request_id == request_id,
                        Turn.status == TurnStatus.awaiting_approval,
                    )
                )
            )
            .scalars()
            .first()
        )
    if turn_id:
        spawn_resume(turn_id, status)


async def resume_turn(turn_id: str, resolved_status: str) -> None:
    """Continue a parked turn. No-op unless it is still awaiting approval.

    On approval the pending tool call runs under the grant. On deny, revoke,
    or expiry the operator is told the call was denied and reports that.
    A second call after the turn has left ``awaiting_approval`` does nothing.
    Failures mark the turn ``failed`` and are not re-raised.
    """
    snapshot = await _claim(turn_id)
    if snapshot is None:
        return

    registry_task = get_task_registry().register(snapshot["session_id"], "Resuming")
    registry_task.current_step = "Resuming after approval"
    try:
        final_text = await _finish(turn_id, snapshot, resolved_status)
    except TurnParked:
        return
    except Exception as exc:
        logger.exception("turn.resume_failed", turn_id=turn_id, error=str(exc))
        await _set_status(turn_id, TurnStatus.failed, error=str(exc)[:2000])
        return
    finally:
        get_task_registry().unregister(snapshot["session_id"], registry_task.id)

    if not await _set_status(turn_id, TurnStatus.completed, only_if=TurnStatus.running):
        return
    await _deliver(snapshot, final_text)
    await _close_scheduled_task(snapshot["session_id"], final_text)


async def _claim(turn_id: str) -> dict[str, Any] | None:
    from vigilus.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            update(Turn)
            .where(Turn.id == turn_id, Turn.status == TurnStatus.awaiting_approval)
            .values(status=TurnStatus.running)
        )
        await db.commit()
        if not result.rowcount:
            return None
        turn = await db.get(Turn, turn_id)
        if turn is None:
            return None
        token = None
        if turn.jit_request_id:
            req = await db.get(JitRequest, turn.jit_request_id)
            if req is not None and req.status == JitStatus.approved and req.token_id:
                token = req.token_id
        return {
            "session_id": turn.session_id,
            "operator_id": turn.operator_id,
            "pending_call": dict(turn.pending_call or {}),
            "operator_messages": list(turn.operator_messages or []),
            "jit_request_id": turn.jit_request_id,
            "deliver_to": turn.deliver_to,
            "unattended": bool(turn.unattended),
            "origin": turn.origin or "web",
            "jit_token": token,
        }


async def _finish(turn_id: str, snapshot: dict[str, Any], resolved_status: str) -> str:
    from sqlalchemy.orm import selectinload

    from vigilus.core.operator_runtime import OperatorRuntime
    from vigilus.core.orchestrator_loop import run_orchestrator
    from vigilus.db.base import get_session_factory
    from vigilus.db.models import Operator, OperatorTool, Provider
    from vigilus.providers.registry import build_provider

    pending = snapshot["pending_call"]
    continuation = dict(pending.get("continuation") or {})
    if not continuation:
        raise RuntimeError("Parked turn has no orchestrator continuation")
    if not snapshot["operator_id"]:
        raise RuntimeError("Parked turn has no operator")

    factory = get_session_factory()
    async with factory() as db:
        operator = (
            await db.execute(
                select(Operator)
                .options(
                    selectinload(Operator.operator_tools).selectinload(OperatorTool.tool),
                    selectinload(Operator.provider),
                )
                .where(Operator.id == snapshot["operator_id"])
            )
        ).scalar_one()
        provider_row = None
        if continuation.get("provider_id"):
            provider_row = await db.get(Provider, continuation["provider_id"])
        if provider_row is None:
            provider_row = operator.provider
        if provider_row is None:
            raise RuntimeError("No provider available to resume the turn")

        approved = resolved_status == "approved"
        park = TurnParkContext(
            session_id=snapshot["session_id"],
            origin=snapshot["origin"],
            deliver_to=snapshot["deliver_to"],
            unattended=snapshot["unattended"],
            operator_id=snapshot["operator_id"],
            turn_id=turn_id,
            continuation=continuation,
        )
        runtime = OperatorRuntime(operator, fallback_provider=provider_row)
        messages = load_messages(snapshot["operator_messages"])
        final_msgs, tool_history = await runtime.continue_after_park(
            messages,
            pending,
            approved=approved,
            jit_token=snapshot["jit_token"] if approved else None,
            session_id=snapshot["session_id"],
            unattended=snapshot["unattended"],
            park=park,
            continuation=continuation,
        )

        operator_text = ""
        for msg in final_msgs:
            if msg.role == "assistant" and isinstance(msg.content, str) and msg.content:
                operator_text = msg.content
        delegation_result = {
            "status": "success",
            "operator": operator.name,
            "response": operator_text,
            "tool_calls": tool_history,
        }
        summary = format_delegation_result(delegation_result)
        history = load_messages(continuation.get("history"))
        if continuation.get("mode") == "native":
            history.append(
                LLMMessage(
                    role="tool",
                    name="delegate",
                    tool_use_id=continuation.get("orchestrator_tool_use_id"),
                    content=summary,
                )
            )
        else:
            history.append(
                LLMMessage(role="assistant", content=continuation.get("response_text") or "")
            )
            history.append(
                LLMMessage(
                    role="user",
                    content=frame_delegation_result(operator.name, summary),
                )
            )

        provider = build_provider(provider_row)
        model = continuation.get("model") or getattr(provider, "default_model", None)
        used = int(continuation.get("delegations_used") or 0) + 1
        cap = int(continuation.get("max_delegations") or 5)
        provider_type = (
            provider_row.type.value
            if hasattr(provider_row.type, "value")
            else str(provider_row.type)
        )
        fresh = await run_orchestrator(
            history,
            provider,
            continuation.get("system_prompt") or "",
            db=db,
            session_id=snapshot["session_id"],
            provider_id=provider_row.id,
            provider_type=provider_type,
            model=model,
            cached_system=continuation.get("cached_system"),
            unattended=snapshot["unattended"],
            max_delegations=max(cap - used, 0),
            park=park,
        )

        rows = list(continuation.get("new_messages") or [])
        rows.append(
            {
                "role": "tool",
                "content": {
                    "operator": operator.name,
                    "result": summary,
                    "status": delegation_result["status"],
                },
                "operator_id": operator.name,
            }
        )
        rows.extend(fresh)

        final_text = ""
        for item in rows:
            role = MessageRole(item["role"])
            content = item["content"]
            db.add(
                Message(
                    session_id=snapshot["session_id"],
                    role=role,
                    content=content,
                    operator_id=item.get("operator_id"),
                )
            )
            if role == MessageRole.assistant and isinstance(content, str) and content:
                final_text = content
        await db.commit()
        return final_text or operator_text


async def _set_status(
    turn_id: str,
    status: TurnStatus,
    *,
    error: str | None = None,
    only_if: TurnStatus | None = None,
) -> bool:
    from vigilus.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        stmt = update(Turn).where(Turn.id == turn_id)
        if only_if is not None:
            stmt = stmt.where(Turn.status == only_if)
        values: dict[str, Any] = {"status": status}
        if error is not None:
            values["error"] = error
        result = await db.execute(stmt.values(**values))
        await db.commit()
        return bool(result.rowcount)


async def _deliver(snapshot: dict[str, Any], final_text: str) -> None:
    deliver_to = snapshot.get("deliver_to")
    if not deliver_to:
        return
    from vigilus.core.scheduler import _deliver_to_channel

    await _deliver_to_channel(deliver_to, final_text, name="Vigilus")


async def _close_scheduled_task(session_id: str, final_text: str) -> None:
    from vigilus.db.base import get_session_factory
    from vigilus.db.models import ScheduledTask

    factory = get_session_factory()
    async with factory() as db:
        tasks = (
            (await db.execute(select(ScheduledTask).where(ScheduledTask.last_status == "awaiting_approval")))
            .scalars()
            .all()
        )
        for task in tasks:
            last = task.last_result or {}
            if last.get("session_id") != session_id:
                continue
            task.last_status = "success"
            task.last_result = {**last, "status": "success", "summary": (final_text or "")[:2000]}
        await db.commit()
