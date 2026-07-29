"""Record and aggregate LLM token usage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.openrouter_pricing import estimate_openrouter_cost, get_openrouter_prices
from vigilus.core.orchestrator import get_app_timezone
from vigilus.db.base import get_session_factory
from vigilus.db.models import LlmUsage, Operator, UsageActorType

logger = structlog.get_logger(__name__)

_VALID_WINDOWS = frozenset({"today", "7d", "30d", "all"})

_PROVIDER_NAMES = {
    "openrouter": "OpenRouter",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openai_compat": "OpenAI Compatible",
    "google": "Google",
    "custom": "Custom",
}


def window_start(
    window: str, *, now: datetime | None = None, tz: ZoneInfo | None = None
) -> datetime | None:
    """Return the inclusive lower bound for a usage window, or None for ``all``."""
    if window not in _VALID_WINDOWS:
        raise ValueError(f"unknown usage window: {window!r}")
    if window == "all":
        return None

    now = now or datetime.now(UTC)
    tz = tz or get_app_timezone()

    if window == "today":
        local_midnight = now.astimezone(tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return local_midnight.astimezone(UTC)
    if window == "7d":
        return now - timedelta(days=7)
    return now - timedelta(days=30)


async def record_llm_usage(
    *,
    usage: dict[str, int],
    actor_type: UsageActorType | str,
    operator_id: str | None = None,
    session_id: str | None = None,
    provider_id: str | None = None,
    provider_type: str | None = None,
    model: str | None = None,
) -> None:
    """Best-effort insert of one completion's token usage (own DB session)."""
    try:
        if not usage:
            return
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        if input_tokens <= 0 and output_tokens <= 0:
            return

        cost = None
        ptype = (provider_type or "").lower() or None
        if ptype == "openrouter" and model:
            prices = await get_openrouter_prices()
            cost = estimate_openrouter_cost(
                model, input_tokens, output_tokens, prices=prices
            )

        factory = get_session_factory()
        async with factory() as session:
            session.add(
                LlmUsage(
                    actor_type=(
                        UsageActorType(actor_type)
                        if isinstance(actor_type, str)
                        else actor_type
                    ),
                    operator_id=operator_id,
                    session_id=session_id,
                    provider_id=provider_id,
                    provider_type=ptype,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost_usd=cost,
                )
            )
            await session.commit()
    except Exception as e:
        logger.warning("usage.record_failed", error=str(e))


def _bucket() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": None,
    }


def _add_tokens(bucket: dict, row: LlmUsage) -> None:
    bucket["input_tokens"] += row.input_tokens
    bucket["output_tokens"] += row.output_tokens
    bucket["total_tokens"] += row.input_tokens + row.output_tokens
    if row.estimated_cost_usd is not None:
        if bucket["estimated_cost_usd"] is None:
            bucket["estimated_cost_usd"] = 0.0
        bucket["estimated_cost_usd"] += row.estimated_cost_usd


async def get_usage_summary(db: AsyncSession, window: str) -> dict:
    """Aggregate usage rows for ``window`` into the API response shape."""
    start = window_start(window)
    stmt = select(LlmUsage)
    if start is not None:
        stmt = stmt.where(LlmUsage.created_at >= start)
    rows = list((await db.execute(stmt)).scalars().all())

    totals = _bucket()
    cost_incomplete = False
    for row in rows:
        _add_tokens(totals, row)
        if row.estimated_cost_usd is None:
            cost_incomplete = True
    if not rows:
        cost_incomplete = False

    orch = {
        "actor_type": UsageActorType.orchestrator.value,
        "operator_id": None,
        "name": "Vigilus",
        **_bucket(),
    }
    operators: dict[str | None, dict] = {}
    for row in rows:
        if row.actor_type == UsageActorType.orchestrator:
            _add_tokens(orch, row)
            continue
        key = row.operator_id
        if key not in operators:
            operators[key] = {
                "actor_type": UsageActorType.operator.value,
                "operator_id": key,
                "name": "Unknown operator",
                **_bucket(),
            }
        _add_tokens(operators[key], row)

    if operators:
        op_ids = [oid for oid in operators if oid is not None]
        if op_ids:
            result = await db.execute(select(Operator).where(Operator.id.in_(op_ids)))
            names = {op.id: op.name for op in result.scalars().all()}
            for oid, bucket in operators.items():
                if oid is not None and oid in names:
                    bucket["name"] = names[oid]

    by_actor = [orch, *operators.values()]

    providers: dict[str | None, dict] = {}
    for row in rows:
        key = row.provider_type
        if key not in providers:
            providers[key] = {
                "provider_type": key,
                "provider_id": None,
                "name": _PROVIDER_NAMES.get(key or "", key or "Unknown"),
                **_bucket(),
            }
        _add_tokens(providers[key], row)

    return {
        "window": window,
        "cost_incomplete": cost_incomplete,
        "totals": totals,
        "by_actor": by_actor,
        "by_provider": list(providers.values()),
    }
