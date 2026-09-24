"""Monthly LLM budget tracking with hard-stop enforcement.

A single global budget (stored on the orchestrator config) plus optional
per-operator budgets cap the estimated LLM spend for the current calendar
month. Once a limit is hit, the orchestrator and operator loops stop making
LLM calls and report the stop instead of silently running up the bill.
Spending is measured from the ``llm_usage`` ledger, so enforcement always
reflects what was actually metered. Raising the budget (or the month
rolling over) resumes work immediately — nothing is disabled persistently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.orchestrator import get_app_timezone, load_orchestrator_config
from vigilus.db.models import LlmUsage, Operator

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BudgetStatus:
    """Result of one budget check for a scope (the platform or one operator)."""

    scope: str
    limit_usd: float
    spent_usd: float

    @property
    def exceeded(self) -> bool:
        return self.spent_usd >= self.limit_usd

    @property
    def percent_used(self) -> float:
        if self.limit_usd <= 0:
            return 0.0
        return (self.spent_usd / self.limit_usd) * 100

    def message(self) -> str:
        return (
            f"⚠️ Monthly budget limit reached ({self.scope}): "
            f"${self.spent_usd:.2f} of the ${self.limit_usd:.2f} monthly cap has "
            "been spent. LLM calls are paused — raise the budget in Settings to "
            "resume, or wait for the month to reset."
        )


def month_start(now: datetime | None = None, tz: ZoneInfo | None = None) -> datetime:
    """Inclusive lower bound of the current calendar month (app tz), as UTC."""
    tz = tz or get_app_timezone()
    now = now or datetime.now(UTC)
    local = now.astimezone(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def global_limit() -> float | None:
    """The platform-wide monthly budget, or None when unset/disabled."""
    value = load_orchestrator_config().monthly_budget_usd
    if value is None or value <= 0:
        return None
    return float(value)


async def month_spend(db: AsyncSession, *, operator_id: str | None = None) -> float:
    """Sum the estimated cost recorded since the start of the month.

    Without ``operator_id`` this is the whole platform's spend (orchestrator
    plus every operator); with it, just that operator's share.
    """
    stmt = select(func.coalesce(func.sum(LlmUsage.estimated_cost_usd), 0.0)).where(
        LlmUsage.created_at >= month_start()
    )
    if operator_id is not None:
        stmt = stmt.where(LlmUsage.operator_id == operator_id)
    return float((await db.execute(stmt)).scalar_one() or 0.0)


async def check_global_budget(db: AsyncSession) -> BudgetStatus | None:
    """Global budget status, or None when no global budget is configured."""
    limit = global_limit()
    if limit is None:
        return None
    return BudgetStatus("Vigilus", limit, await month_spend(db))


async def check_operator_budget(
    db: AsyncSession, operator: Operator
) -> BudgetStatus | None:
    """One operator's budget status, or None when it has no budget set."""
    limit = operator.monthly_budget_usd
    if limit is None or limit <= 0:
        return None
    return BudgetStatus(operator.name, float(limit), await month_spend(db, operator_id=operator.id))


async def turn_budget_stop(db: AsyncSession, operator: Operator | None = None) -> str | None:
    """Return a human-readable stop message when a budget is exhausted.

    Checks the global budget always, and the operator's own budget when an
    operator is given. Fails open: if the check itself errors, the turn
    proceeds rather than breaking on a non-budget problem.
    """
    try:
        statuses = []
        overall = await check_global_budget(db)
        if overall is not None and overall.exceeded:
            statuses.append(overall)
        if operator is not None:
            own = await check_operator_budget(db, operator)
            if own is not None and own.exceeded:
                statuses.append(own)
        if not statuses:
            return None
        return "\n\n".join(s.message() for s in statuses)
    except Exception as e:  # noqa: BLE001 — enforcement must never break turns
        logger.warning("budget.check_failed", error=str(e))
        return None


async def build_budget_block(db: AsyncSession) -> dict:
    """Budget overview for the usage API: platform totals + per-operator caps."""
    limit = global_limit()
    spent = await month_spend(db)
    rows = (
        (
            await db.execute(
                select(Operator).where(Operator.monthly_budget_usd.is_not(None))  # noqa: E711
            )
        )
        .scalars()
        .all()
    )
    operators = []
    for op in rows:
        op_limit = float(op.monthly_budget_usd or 0)
        op_spent = await month_spend(db, operator_id=op.id)
        operators.append(
            {
                "operator_id": op.id,
                "name": op.name,
                "limit_usd": op_limit,
                "spent_usd": op_spent,
                "exceeded": op_spent >= op_limit,
            }
        )
    return {
        "monthly_limit_usd": limit,
        "month_spent_usd": spent,
        "percent_used": (spent / limit * 100) if limit else None,
        "operators": operators,
    }
