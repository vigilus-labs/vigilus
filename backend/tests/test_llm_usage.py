"""Tests for LLM usage metering (model, record, aggregate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from vigilus.core.llm_usage import get_usage_summary, record_llm_usage, window_start
from vigilus.db.models import (
    LlmUsage,
    Operator,
    PermissionLevel,
    Provider,
    ProviderType,
    UsageActorType,
)


@pytest.mark.asyncio
async def test_llm_usage_model_round_trip(db_session):
    row = LlmUsage(
        actor_type=UsageActorType.orchestrator,
        operator_id=None,
        session_id=None,
        provider_id=None,
        provider_type="openrouter",
        model="openai/gpt-4o-mini",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.001,
    )
    db_session.add(row)
    await db_session.commit()

    loaded = (await db_session.execute(select(LlmUsage))).scalar_one()
    assert loaded.actor_type == UsageActorType.orchestrator
    assert loaded.input_tokens == 100
    assert loaded.output_tokens == 50
    assert loaded.provider_type == "openrouter"
    assert loaded.estimated_cost_usd == pytest.approx(0.001)
    assert loaded.created_at is not None


def test_window_start_today_uses_timezone():
    tz = ZoneInfo("America/Denver")
    now = datetime(2026, 7, 29, 18, 30, tzinfo=UTC)
    start = window_start("today", now=now, tz=tz)
    local_midnight = now.astimezone(tz).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert start == local_midnight.astimezone(UTC)


def test_window_start_7d_and_all():
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    assert window_start("7d", now=now, tz=ZoneInfo("UTC")) == now - timedelta(days=7)
    assert window_start("30d", now=now, tz=ZoneInfo("UTC")) == now - timedelta(days=30)
    assert window_start("all", now=now, tz=ZoneInfo("UTC")) is None


@pytest.mark.asyncio
async def test_record_skips_empty_usage(db_session):
    await record_llm_usage(
        usage={},
        actor_type=UsageActorType.orchestrator,
        provider_type="openrouter",
        model="x",
    )
    assert (await db_session.execute(select(LlmUsage))).scalars().first() is None


@pytest.mark.asyncio
async def test_record_swallows_errors(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("vigilus.core.llm_usage.get_session_factory", boom)
    await record_llm_usage(
        usage={"input_tokens": 1, "output_tokens": 1},
        actor_type=UsageActorType.orchestrator,
        provider_type="anthropic",
        model="x",
    )  # must not raise


@pytest.mark.asyncio
async def test_record_and_summarize_actors(db_session, monkeypatch):
    monkeypatch.setattr(
        "vigilus.core.llm_usage.get_cached_openrouter_prices",
        lambda: {"m": (0.001, 0.002)},
    )
    monkeypatch.setattr(
        "vigilus.core.llm_usage.schedule_openrouter_price_refresh", lambda: None
    )

    provider = Provider(
        name="or",
        type=ProviderType.openrouter,
        default_model="m",
        enabled=True,
    )
    db_session.add(provider)
    op = Operator(
        name="Infra",
        description="d",
        permission_level=PermissionLevel.read,
        provider_id=None,
    )
    db_session.add(op)
    await db_session.commit()

    await record_llm_usage(
        usage={"input_tokens": 10, "output_tokens": 5},
        actor_type=UsageActorType.orchestrator,
        provider_id=provider.id,
        provider_type="openrouter",
        model="m",
    )
    await record_llm_usage(
        usage={"input_tokens": 100, "output_tokens": 20},
        actor_type=UsageActorType.operator,
        operator_id=op.id,
        provider_id=provider.id,
        provider_type="openrouter",
        model="m",
    )
    # Non-OpenRouter: tokens yes, cost null
    await record_llm_usage(
        usage={"input_tokens": 7, "output_tokens": 3},
        actor_type=UsageActorType.operator,
        operator_id=op.id,
        provider_type="anthropic",
        model="claude",
    )

    summary = await get_usage_summary(db_session, "all")
    assert summary["totals"]["input_tokens"] == 117
    assert summary["totals"]["output_tokens"] == 28
    assert summary["cost_incomplete"] is True
    names = {a["name"]: a for a in summary["by_actor"]}
    assert "Vigilus" in names
    assert names["Vigilus"]["total_tokens"] == 15
    assert names["Infra"]["total_tokens"] == 130
    assert any(p["provider_type"] == "openrouter" for p in summary["by_provider"])


@pytest.mark.asyncio
async def test_operator_runtime_records_usage(db_session, monkeypatch):
    from vigilus.core.operator_runtime import OperatorRuntime
    from vigilus.providers.base import LLMMessage, LLMResponse

    provider = Provider(
        name="or2",
        type=ProviderType.openrouter,
        default_model="m",
        enabled=True,
        api_key=None,
    )
    db_session.add(provider)
    op = Operator(
        name="MeteredOp",
        description="d",
        permission_level=PermissionLevel.read,
        provider_id=None,
        model="m",
    )
    db_session.add(op)
    await db_session.commit()
    await db_session.refresh(provider)
    from sqlalchemy.orm.attributes import set_committed_value

    set_committed_value(op, "provider", provider)
    set_committed_value(op, "operator_tools", [])

    class FakeProvider:
        default_model = "m"

        async def complete(self, **kwargs):
            return LLMResponse(
                content="done",
                usage={"input_tokens": 11, "output_tokens": 3},
            )

    monkeypatch.setattr(
        "vigilus.core.llm_usage.get_cached_openrouter_prices",
        lambda: {"m": (0.0, 0.0)},
    )
    monkeypatch.setattr(
        "vigilus.core.llm_usage.schedule_openrouter_price_refresh", lambda: None
    )

    runtime = OperatorRuntime(op, fallback_provider=provider)
    runtime.provider = FakeProvider()

    async def _nop_tools(self):
        return []

    async def _nop_prompt(self, tools):
        return None

    monkeypatch.setattr(OperatorRuntime, "_get_tools", _nop_tools)
    monkeypatch.setattr(OperatorRuntime, "_build_system_prompt", _nop_prompt)

    await runtime.run(
        [LLMMessage(role="user", content="hi")],
        session_id="sess-1",
        max_iterations=1,
    )

    rows = (await db_session.execute(select(LlmUsage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_type == UsageActorType.operator
    assert rows[0].operator_id == op.id
    assert rows[0].input_tokens == 11
    assert rows[0].session_id == "sess-1"
