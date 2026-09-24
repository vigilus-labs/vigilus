"""Monthly LLM budget enforcement — aggregation, hard stops, API surface.

Budgets cap the estimated spend of the current calendar month: one
platform-wide cap on the orchestrator config plus optional per-operator caps.
When a cap is hit, the orchestrator and operator loops must stop *before*
making another LLM call and report the stop in place of further work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from vigilus.api.chat import _run_orchestrator
from vigilus.core import budget as budget_mod
from vigilus.core import orchestrator as orch
from vigilus.core.budget import (
    build_budget_block,
    check_global_budget,
    check_operator_budget,
    month_spend,
    month_start,
    turn_budget_stop,
)
from vigilus.db.models import (
    LlmUsage,
    Operator,
    PermissionLevel,
    Provider,
    ProviderType,
    UsageActorType,
)
from vigilus.providers.base import AgentLLM, LLMMessage, LLMResponse


def _spend_row(cost: float, *, operator_id: str | None = None, when: datetime | None = None):
    return LlmUsage(
        actor_type=UsageActorType.operator if operator_id else UsageActorType.orchestrator,
        operator_id=operator_id,
        provider_type="anthropic",
        model="claude-opus-5",
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=cost,
        created_at=when or datetime.now(UTC),
    )


# ── Aggregation ────────────────────────────────────────────


def test_month_start_is_local_calendar_month():
    tz = ZoneInfo("America/Denver")
    now = datetime(2026, 9, 10, 18, 30, tzinfo=UTC)
    start = month_start(now=now, tz=tz)
    expected_local = now.astimezone(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    assert start == expected_local.astimezone(UTC)


async def test_month_spend_totals_and_per_operator(db_session):
    op = Operator(name="BudgetOp", description="d", permission_level=PermissionLevel.read)
    db_session.add(op)
    await db_session.commit()

    db_session.add(_spend_row(2.0))
    db_session.add(_spend_row(3.0, operator_id=op.id))
    db_session.add(_spend_row(1.0, operator_id=op.id))
    # A row from before this month must not count toward the budget.
    db_session.add(_spend_row(50.0, when=month_start() - timedelta(days=1)))
    await db_session.commit()

    assert await month_spend(db_session) == pytest.approx(6.0)
    assert await month_spend(db_session, operator_id=op.id) == pytest.approx(4.0)


async def test_no_rows_means_zero_spend(db_session):
    assert await month_spend(db_session) == 0.0


# ── Checks ─────────────────────────────────────────────────


async def test_global_budget_exceeded(db_session, monkeypatch):
    db_session.add(_spend_row(6.0))
    await db_session.commit()
    monkeypatch.setattr(budget_mod, "global_limit", lambda: 5.0)

    status = await check_global_budget(db_session)
    assert status is not None
    assert status.exceeded
    assert status.percent_used == pytest.approx(120.0)
    assert "Vigilus" in status.message()


async def test_global_budget_unset_returns_none(db_session):
    assert await check_global_budget(db_session) is None


async def test_operator_budget(db_session):
    op = Operator(
        name="CappedOp",
        description="d",
        permission_level=PermissionLevel.read,
        monthly_budget_usd=5.0,
    )
    db_session.add(op)
    await db_session.commit()
    db_session.add(_spend_row(4.0, operator_id=op.id))
    await db_session.commit()

    status = await check_operator_budget(db_session, op)
    assert status is not None
    assert not status.exceeded
    assert status.spent_usd == pytest.approx(4.0)

    db_session.add(_spend_row(2.0, operator_id=op.id))
    await db_session.commit()
    assert (await check_operator_budget(db_session, op)).exceeded


async def test_turn_budget_stop_reports_both_scopes(db_session, monkeypatch):
    op = Operator(
        name="DoubleCapped",
        description="d",
        permission_level=PermissionLevel.read,
        monthly_budget_usd=1.0,
    )
    db_session.add(op)
    await db_session.commit()
    db_session.add(_spend_row(9.0, operator_id=op.id))
    await db_session.commit()

    monkeypatch.setattr(budget_mod, "global_limit", lambda: 2.0)
    stop = await turn_budget_stop(db_session, operator=op)
    assert stop is not None
    assert "Vigilus" in stop
    assert "DoubleCapped" in stop

    # Under both caps → no stop.
    async with db_session.begin_nested():
        pass
    from sqlalchemy import delete

    await db_session.execute(delete(LlmUsage))
    await db_session.commit()
    assert await turn_budget_stop(db_session, operator=op) is None


async def test_turn_budget_stop_fails_open(db_session, monkeypatch):
    """A broken budget check must never break the turn itself."""
    monkeypatch.setattr(budget_mod, "global_limit", lambda: 5.0)

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(budget_mod, "month_spend", boom)
    assert await turn_budget_stop(db_session) is None


# ── Orchestrator loop enforcement ──────────────────────────


class RecordingProvider(AgentLLM):
    """Fails the test if called; records any calls that slip through."""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content="should not be reached")

    async def test_connection(self) -> bool:
        return True


async def test_orchestrator_loop_stops_on_budget(db_session, monkeypatch):
    db_session.add(_spend_row(10.0))
    await db_session.commit()
    monkeypatch.setattr(budget_mod, "global_limit", lambda: 10.0)

    provider = RecordingProvider()
    msgs = await _run_orchestrator(
        [LLMMessage(role="user", content="do something expensive")],
        provider,
        "system prompt",
        db=db_session,
    )

    assert provider.calls == 0
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert "budget" in msgs[0]["content"].lower()


async def test_orchestrator_loop_unaffected_without_budget(db_session):
    class WorkingProvider(AgentLLM):
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, **kwargs) -> LLMResponse:
            self.calls += 1
            return LLMResponse(content="All done.")

        async def test_connection(self) -> bool:
            return True

    provider = WorkingProvider()
    msgs = await _run_orchestrator(
        [LLMMessage(role="user", content="hello")],
        provider,
        "system prompt",
        db=db_session,
    )
    assert provider.calls == 1
    assert msgs[-1]["content"] == "All done."


# ── Operator runtime enforcement ───────────────────────────


async def test_operator_loop_stops_on_own_budget(db_session, monkeypatch):
    from sqlalchemy.orm.attributes import set_committed_value

    from vigilus.core.operator_runtime import OperatorRuntime

    provider = Provider(
        name="budget-prov",
        type=ProviderType.openrouter,
        default_model="m",
        enabled=True,
    )
    db_session.add(provider)
    op = Operator(
        name="OverBudget",
        description="d",
        permission_level=PermissionLevel.read,
        model="m",
        monthly_budget_usd=2.0,
    )
    db_session.add(op)
    await db_session.commit()
    await db_session.refresh(provider)
    set_committed_value(op, "provider", provider)
    set_committed_value(op, "operator_tools", [])

    db_session.add(_spend_row(2.0, operator_id=op.id))
    await db_session.commit()

    class ForbiddenProvider:
        default_model = "m"

        async def complete(self, **kwargs):
            raise AssertionError("provider must not be called when over budget")

    runtime = OperatorRuntime(op, fallback_provider=provider)
    runtime.provider = ForbiddenProvider()

    async def _nop_tools(self):
        return []

    async def _nop_prompt(self, tools):
        return None

    monkeypatch.setattr(OperatorRuntime, "_get_tools", _nop_tools)
    monkeypatch.setattr(OperatorRuntime, "_build_system_prompt", _nop_prompt)

    messages = [LLMMessage(role="user", content="hello")]
    final_msgs, tool_history = await runtime.run(messages, session_id=None, max_iterations=3)

    assert any(t.get("budget_stop") for t in tool_history)
    last = [m for m in final_msgs if m.role == "assistant"][-1]
    assert "OverBudget" in str(last.content)


async def test_operator_loop_runs_when_under_budget(db_session, monkeypatch):
    from sqlalchemy.orm.attributes import set_committed_value

    from vigilus.core.operator_runtime import OperatorRuntime
    from vigilus.providers.base import LLMMessage as LLMMsg

    provider = Provider(
        name="budget-prov-2",
        type=ProviderType.openrouter,
        default_model="m",
        enabled=True,
    )
    db_session.add(provider)
    op = Operator(
        name="UnderBudget",
        description="d",
        permission_level=PermissionLevel.read,
        model="m",
        monthly_budget_usd=100.0,
    )
    db_session.add(op)
    await db_session.commit()
    await db_session.refresh(provider)
    set_committed_value(op, "provider", provider)
    set_committed_value(op, "operator_tools", [])

    db_session.add(_spend_row(1.0, operator_id=op.id))
    await db_session.commit()

    class FakeProvider:
        default_model = "m"

        async def complete(self, **kwargs):
            return LLMResponse(content="done", usage={"input_tokens": 1, "output_tokens": 1})

    runtime = OperatorRuntime(op, fallback_provider=provider)
    runtime.provider = FakeProvider()

    async def _nop_tools(self):
        return []

    async def _nop_prompt(self, tools):
        return None

    monkeypatch.setattr(OperatorRuntime, "_get_tools", _nop_tools)
    monkeypatch.setattr(OperatorRuntime, "_build_system_prompt", _nop_prompt)
    monkeypatch.setattr(
        "vigilus.core.llm_usage.get_cached_openrouter_prices", lambda: {"m": (0.0, 0.0)}
    )
    monkeypatch.setattr("vigilus.core.llm_usage.schedule_openrouter_price_refresh", lambda: None)

    final_msgs, tool_history = await runtime.run(
        [LLMMsg(role="user", content="hello")], session_id=None, max_iterations=3
    )
    assert not any(t.get("budget_stop") for t in tool_history)
    assert final_msgs[-1].content == "done"


# ── API surface ────────────────────────────────────────────


async def test_usage_api_includes_budget_block(db_session, async_client: AsyncClient):
    op = Operator(
        name="ApiBudgetOp",
        description="d",
        permission_level=PermissionLevel.read,
        monthly_budget_usd=10.0,
    )
    db_session.add(op)
    await db_session.commit()
    db_session.add(_spend_row(3.0, operator_id=op.id))
    await db_session.commit()

    res = await async_client.get("/api/usage?window=7d")
    assert res.status_code == 200
    budget = res.json()["budget"]
    assert budget["monthly_limit_usd"] is None
    assert budget["month_spent_usd"] == pytest.approx(3.0)
    entries = {e["operator_id"]: e for e in budget["operators"]}
    assert entries[op.id]["limit_usd"] == 10.0
    assert entries[op.id]["spent_usd"] == pytest.approx(3.0)
    assert entries[op.id]["exceeded"] is False


async def test_orchestrator_config_budget_roundtrip(
    db_session, async_client: AsyncClient, tmp_path, monkeypatch
):
    monkeypatch.setattr(orch, "_config_path", lambda: str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(orch, "_config_cache", None)

    res = await async_client.patch("/api/orchestrator", json={"monthly_budget_usd": 25.5})
    assert res.status_code == 200
    assert res.json()["monthly_budget_usd"] == 25.5

    # GET reflects the persisted value.
    res = await async_client.get("/api/orchestrator")
    assert res.json()["monthly_budget_usd"] == 25.5

    # Zero clears the cap.
    res = await async_client.patch("/api/orchestrator", json={"monthly_budget_usd": 0})
    assert res.status_code == 200
    assert res.json()["monthly_budget_usd"] is None

    # Negative values are rejected.
    res = await async_client.patch("/api/orchestrator", json={"monthly_budget_usd": -1})
    assert res.status_code == 422


async def test_budget_block_shape_without_limits(db_session):
    block = await build_budget_block(db_session)
    assert block["monthly_limit_usd"] is None
    assert block["percent_used"] is None
    assert block["operators"] == []
