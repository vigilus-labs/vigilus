"""Tests for the scheduled tasks API and cron validation."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from vigilus.core.scheduler import next_fire_time, validate_cron

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
