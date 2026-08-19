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
    monkeypatch.setattr("vigilus.core.llm_usage.schedule_openrouter_price_refresh", lambda: None)
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


@pytest.mark.asyncio
async def test_usage_returns_dashboard_sections(async_client, db_session):
    from vigilus.db.models import Session

    session = Session(title="Ops triage")
    db_session.add(session)
    await db_session.commit()

    await record_llm_usage(
        usage={"input_tokens": 1_000_000, "output_tokens": 0},
        actor_type=UsageActorType.orchestrator,
        session_id=session.id,
        provider_type="anthropic",
        model="claude-opus-5",
    )

    resp = await async_client.get("/api/usage", params={"window": "7d"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["by_model"][0]["model"] == "claude-opus-5"
    # Direct providers are now priced from the static table.
    assert body["totals"]["estimated_cost_usd"] == pytest.approx(5.0)
    assert body["cost_incomplete"] is False
    assert body["top_sessions"][0]["title"] == "Ops triage"
    assert body["top_sessions"][0]["session_id"] == session.id
    assert len(body["series"]) == 8
    assert body["series"][-1]["orchestrator_tokens"] == 1_000_000
