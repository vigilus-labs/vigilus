"""HTTP tests for GET /api/usage."""

from __future__ import annotations

import pytest

from vigilus.core.llm_usage import record_llm_usage
from vigilus.db.models import UsageActorType


@pytest.mark.asyncio
async def test_usage_empty(async_client):
    resp = await async_client.get("/api/usage", params={"window": "all"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["window"] == "all"
    assert body["totals"]["total_tokens"] == 0
    assert body["by_actor"][0]["name"] == "Vigilus"
    assert body["by_actor"][0]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_usage_invalid_window(async_client):
    resp = await async_client.get("/api/usage", params={"window": "year"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_usage_aggregates(async_client, db_session, monkeypatch):
    monkeypatch.setattr(
        "vigilus.core.llm_usage.schedule_openrouter_price_refresh", lambda: None
    )
    await record_llm_usage(
        usage={"input_tokens": 40, "output_tokens": 10},
        actor_type=UsageActorType.orchestrator,
        provider_type="anthropic",
        model="claude",
    )
    resp = await async_client.get("/api/usage", params={"window": "all"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["total_tokens"] == 50
    assert body["cost_incomplete"] is True
