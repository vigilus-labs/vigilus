"""Tests for the scheduled tasks API and cron validation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from vigilus.core.scheduler import (
    missed_fire,
    next_fire_time,
    recover_stale_running_tasks,
    validate_cron,
)
from vigilus.db.models import ScheduledTask

VALID_TASK = {
    "name": "Daily security summary",
    "description": "Summarize Wazuh alerts every morning",
    "cron_expression": "0 8 * * *",
    "task_prompt": "Pull the last 24h of Wazuh alerts and summarize anything suspicious.",
    "enabled": True,
}


class TestCronValidation:
    def test_valid_expressions(self):
        for expr in ["0 8 * * *", "*/15 * * * *", "0 2 * * 0", "30 4 1 * *"]:
            assert validate_cron(expr) is None, expr

    def test_invalid_expressions(self):
        for expr in ["not a cron", "99 99 * * *", "* * * *", ""]:
            assert validate_cron(expr) is not None, expr

    def test_next_fire_time(self):
        assert next_fire_time("0 8 * * *") is not None
        assert next_fire_time("garbage") is None

    def test_next_fire_time_respects_tz(self):
        # "0 8 * * *" is 8 AM *local* in the given zone, so the reported fire
        # time stays at hour 8 in that zone but lands on a different absolute
        # instant than the UTC interpretation.
        ny = next_fire_time("0 8 * * *", ZoneInfo("America/New_York"))
        utc = next_fire_time("0 8 * * *", ZoneInfo("UTC"))
        assert ny is not None and utc is not None
        assert ny.hour == 8 and utc.hour == 8
        assert ny.utcoffset() != utc.utcoffset()


class TestSchedulesApi:
    async def test_create_and_list(self, async_client):
        resp = await async_client.post("/api/schedules", json=VALID_TASK)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == VALID_TASK["name"]
        assert body["enabled"] is True
        assert body["next_run_at"] is not None
        assert body["run_count"] == 0

        resp = await async_client.get("/api/schedules")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_create_rejects_bad_cron(self, async_client):
        bad = {**VALID_TASK, "name": "bad cron", "cron_expression": "every day at 8"}
        resp = await async_client.post("/api/schedules", json=bad)
        assert resp.status_code == 422
        assert "cron" in resp.json()["detail"].lower()

    async def test_create_rejects_duplicate_name(self, async_client):
        resp = await async_client.post("/api/schedules", json=VALID_TASK)
        assert resp.status_code == 201
        resp = await async_client.post("/api/schedules", json=VALID_TASK)
        assert resp.status_code == 409

    async def test_create_rejects_unknown_operator(self, async_client):
        bad = {**VALID_TASK, "name": "op task", "operator_id": "no-such-operator"}
        resp = await async_client.post("/api/schedules", json=bad)
        assert resp.status_code == 400

    async def test_update(self, async_client):
        created = (await async_client.post("/api/schedules", json=VALID_TASK)).json()

        resp = await async_client.patch(
            f"/api/schedules/{created['id']}",
            json={"cron_expression": "0 20 * * *", "enabled": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["cron_expression"] == "0 20 * * *"
        assert body["enabled"] is False
        assert body["next_run_at"] is None  # disabled tasks have no next run

    async def test_update_rejects_bad_cron(self, async_client):
        created = (await async_client.post("/api/schedules", json=VALID_TASK)).json()
        resp = await async_client.patch(
            f"/api/schedules/{created['id']}", json={"cron_expression": "bogus"}
        )
        assert resp.status_code == 422

    async def test_delete(self, async_client):
        created = (await async_client.post("/api/schedules", json=VALID_TASK)).json()
        resp = await async_client.delete(f"/api/schedules/{created['id']}")
        assert resp.status_code == 200
        resp = await async_client.get(f"/api/schedules/{created['id']}")
        assert resp.status_code == 404

    async def test_run_now_missing_task(self, async_client):
        resp = await async_client.post("/api/schedules/nonexistent/run")
        assert resp.status_code == 404


class TestMissedFireRecovery:
    """Missed-fire detection and stale 'running' reset (self-healing)."""

    # Fixed timeline: daily cron at 08:00 UTC. The fire for Sep 24 08:00 has
    # already come due by NOW.
    NOW = datetime(2026, 9, 24, 9, 30, tzinfo=UTC)
    UTC_TZ = ZoneInfo("UTC")

    @staticmethod
    def _task(
        *,
        name="selfheal-task",
        cron="0 8 * * *",
        created_at=None,
        last_run_at=None,
        last_status=None,
        enabled=True,
    ):
        return ScheduledTask(
            name=name,
            cron_expression=cron,
            task_prompt="p",
            enabled=enabled,
            created_at=created_at or datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            last_run_at=last_run_at,
            last_status=last_status,
        )

    def test_fresh_task_with_future_fire_not_missed(self):
        # Created after the most recent fire: nothing is owed yet.
        task = self._task(created_at=datetime(2026, 9, 24, 8, 30, tzinfo=UTC))
        assert missed_fire(task, now=self.NOW, tz=self.UTC_TZ) is False

    def test_missed_fire_detected_when_last_run_older_than_fire(self):
        task = self._task(last_run_at=datetime(2026, 9, 21, 8, 0, tzinfo=UTC))
        # Fires on Sep 22, 23, and 24 came due after the last run.
        assert missed_fire(task, now=self.NOW, tz=self.UTC_TZ) is True

    def test_no_miss_when_last_run_is_most_recent_fire(self):
        task = self._task(last_run_at=datetime(2026, 9, 24, 8, 0, tzinfo=UTC))
        assert missed_fire(task, now=self.NOW, tz=self.UTC_TZ) is False

    def test_never_run_with_fire_since_creation_is_missed(self):
        task = self._task(created_at=datetime(2026, 9, 23, 6, 0, tzinfo=UTC))
        # Fires on Sep 23 08:00 and Sep 24 08:00 happened after creation.
        assert missed_fire(task, now=self.NOW, tz=self.UTC_TZ) is True

    def test_fire_before_task_creation_does_not_count(self):
        task = self._task(created_at=datetime(2026, 9, 24, 8, 30, tzinfo=UTC), last_run_at=None)
        assert missed_fire(task, now=self.NOW, tz=self.UTC_TZ) is False

    def test_bad_cron_is_never_missed(self):
        task = self._task(cron="garbage")
        assert missed_fire(task, now=self.NOW, tz=self.UTC_TZ) is False

    def test_naive_timestamps_are_treated_as_utc(self):
        task = self._task(last_run_at=datetime(2026, 9, 24, 8, 0))  # naive (SQLite round-trip)
        assert missed_fire(task, now=self.NOW, tz=self.UTC_TZ) is False

    async def test_recover_stale_running_tasks(self, db_session):
        stuck = self._task(name="stuck-task", last_status="running", last_run_at=datetime.now(UTC))
        stuck.last_result = {"status": "running", "session_id": "sess-1"}
        ok = self._task(name="ok-task", last_status="success", last_run_at=datetime.now(UTC))
        db_session.add_all([stuck, ok])
        await db_session.commit()

        count = await recover_stale_running_tasks()

        assert count == 1
        await db_session.refresh(stuck)
        await db_session.refresh(ok)
        assert stuck.last_status == "error"
        assert stuck.last_result["error"] == "Interrupted — backend restarted mid-run"
        # The original session link is preserved for review on /chat.
        assert stuck.last_result["session_id"] == "sess-1"
        assert ok.last_status == "success"

    async def test_catch_up_runs_missed_task_once(self, db_session, monkeypatch):
        """On startup, a task that missed a fire while down runs exactly once."""
        now = datetime.now(UTC)
        # Last ran at (or just before) the previous expected fire → one owed.
        today_eight = now.replace(hour=8, minute=0, second=0, microsecond=0)
        previous_fire = today_eight if today_eight <= now else today_eight - timedelta(days=1)
        task = self._task(last_run_at=previous_fire - timedelta(days=1))
        db_session.add(task)
        await db_session.commit()

        fired: list[str] = []

        async def fake_execute(task_id, *, force=False):
            fired.append(task_id)
            return {"status": "success", "summary": ""}

        monkeypatch.setattr("vigilus.core.scheduler.execute_scheduled_task", fake_execute)

        engine = SchedulerEngineWithStub()
        await engine.start_with_stub()
        # Let the background catch-up task run to completion.
        for _ in range(3):
            await asyncio.sleep(0)

        assert fired == [task.id]
        engine.shutdown_sync()

    async def test_no_catch_up_when_runs_are_current(self, db_session, monkeypatch):
        now = datetime.now(UTC)
        today_eight = now.replace(hour=8, minute=0, second=0, microsecond=0)
        last_run = today_eight if today_eight <= now else today_eight - timedelta(days=1)
        task = self._task(last_run_at=last_run)
        db_session.add(task)
        await db_session.commit()

        fired: list[str] = []

        async def fake_execute(task_id, *, force=False):
            fired.append(task_id)
            return {"status": "success", "summary": ""}

        monkeypatch.setattr("vigilus.core.scheduler.execute_scheduled_task", fake_execute)

        engine = SchedulerEngineWithStub()
        await engine.start_with_stub()
        for _ in range(3):
            await asyncio.sleep(0)

        assert fired == []
        engine.shutdown_sync()


class SchedulerEngineWithStub:
    """SchedulerEngine wired to a stub job store so tests don't need a live
    AsyncIOScheduler — only the catch-up side effects under test."""

    def __init__(self):
        from vigilus.core.scheduler import SchedulerEngine

        self.engine = SchedulerEngine()
        self.jobs: list[tuple] = []

        class StubScheduler:
            def __init__(self, outer):
                self._outer = outer

            def add_job(self, *args, **kwargs):
                self._outer.jobs.append((args, kwargs))

        self.engine._scheduler = StubScheduler(self)

    async def start_with_stub(self):
        await self.engine._load_enabled_tasks()

    def shutdown_sync(self):
        self.engine._scheduler = None
