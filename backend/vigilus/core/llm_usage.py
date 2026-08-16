"""Record and aggregate LLM token usage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.model_pricing import estimate_static_cost
from vigilus.core.openrouter_pricing import (
    estimate_openrouter_cost,
    get_cached_openrouter_prices,
    schedule_openrouter_price_refresh,
)
from vigilus.core.orchestrator import get_app_timezone
from vigilus.db.base import get_session_factory
from vigilus.db.models import LlmUsage, Operator, Session, UsageActorType

logger = structlog.get_logger(__name__)

_VALID_WINDOWS = frozenset({"today", "7d", "30d", "all"})

# Cap zero-filled series buckets so an "all" window over a long-lived install
# does not return thousands of points.
_MAX_FILLED_BUCKETS = 120

# Heaviest sessions surfaced by the dashboard.
_TOP_SESSIONS_LIMIT = 5

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
            # Never await network on the chat hot path — cache only + bg refresh.
            prices = get_cached_openrouter_prices()
            cost = estimate_openrouter_cost(
                model, input_tokens, output_tokens, prices=prices
            )
            schedule_openrouter_price_refresh()
        elif model:
            # Direct providers publish no price API — use the static list-price
            # table. Unknown models stay unpriced (cost_incomplete).
            cost = estimate_static_cost(ptype, model, input_tokens, output_tokens)

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

    models: dict[tuple[str | None, str | None], dict] = {}
    for row in rows:
        key = (row.provider_type, row.model)
        if key not in models:
            models[key] = {
                "provider_type": row.provider_type,
                "model": row.model,
                "name": row.model or "Unknown model",
                **_bucket(),
            }
        _add_tokens(models[key], row)
    by_model = sorted(
        models.values(), key=lambda m: m["total_tokens"], reverse=True
    )

    return {
        "window": window,
        "cost_incomplete": cost_incomplete,
        "totals": totals,
        "by_actor": by_actor,
        "by_provider": list(providers.values()),
        "by_model": by_model,
        "series": _build_series(rows, window, start),
        "top_sessions": await _build_top_sessions(db, rows),
    }


def _as_utc(value: datetime) -> datetime:
    """Treat naive timestamps (SQLite round-trips) as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _build_series(
    rows: list[LlmUsage], window: str, start: datetime | None
) -> list[dict]:
    """Bucket usage over time, split by orchestrator vs operators.

    ``today`` buckets hourly; every other window buckets by local calendar day.
    Empty buckets are zero-filled so the chart shows real gaps, up to
    ``_MAX_FILLED_BUCKETS``; beyond that only buckets with data are returned.
    """
    tz = get_app_timezone()
    hourly = window == "today"
    fmt = "%Y-%m-%dT%H:00" if hourly else "%Y-%m-%d"

    def key_of(dt: datetime) -> str:
        return _as_utc(dt).astimezone(tz).strftime(fmt)

    buckets: dict[str, dict] = {}

    def ensure(key: str) -> dict:
        if key not in buckets:
            buckets[key] = {
                "bucket": key,
                "orchestrator_tokens": 0,
                "operator_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": None,
            }
        return buckets[key]

    now = datetime.now(UTC)
    lower = start
    if lower is None and rows:
        lower = min(_as_utc(r.created_at) for r in rows)
    if lower is not None:
        step = timedelta(hours=1) if hourly else timedelta(days=1)
        local = _as_utc(lower).astimezone(tz)
        cursor = local.replace(minute=0, second=0, microsecond=0)
        if not hourly:
            cursor = cursor.replace(hour=0)
        filled = 0
        while cursor <= now.astimezone(tz) and filled < _MAX_FILLED_BUCKETS:
            ensure(cursor.strftime(fmt))
            cursor += step
            filled += 1

    for row in rows:
        bucket = ensure(key_of(row.created_at))
        tokens = row.input_tokens + row.output_tokens
        bucket["total_tokens"] += tokens
        if row.actor_type == UsageActorType.orchestrator:
            bucket["orchestrator_tokens"] += tokens
        else:
            bucket["operator_tokens"] += tokens
        if row.estimated_cost_usd is not None:
            if bucket["estimated_cost_usd"] is None:
                bucket["estimated_cost_usd"] = 0.0
            bucket["estimated_cost_usd"] += row.estimated_cost_usd

    return [buckets[k] for k in sorted(buckets)]


async def _build_top_sessions(db: AsyncSession, rows: list[LlmUsage]) -> list[dict]:
    """Heaviest chat sessions in the window, newest activity first on ties."""
    sessions: dict[str, dict] = {}
    for row in rows:
        if not row.session_id:
            continue
        if row.session_id not in sessions:
            sessions[row.session_id] = {
                "session_id": row.session_id,
                "title": None,
                "last_used_at": _as_utc(row.created_at),
                **_bucket(),
            }
        entry = sessions[row.session_id]
        _add_tokens(entry, row)
        entry["last_used_at"] = max(entry["last_used_at"], _as_utc(row.created_at))

    if not sessions:
        return []

    top = sorted(
        sessions.values(),
        key=lambda s: (s["total_tokens"], s["last_used_at"]),
        reverse=True,
    )[:_TOP_SESSIONS_LIMIT]

    result = await db.execute(
        select(Session).where(Session.id.in_([s["session_id"] for s in top]))
    )
    titles = {s.id: s.title for s in result.scalars().all()}
    for entry in top:
        entry["title"] = titles.get(entry["session_id"]) or "Untitled session"
    return top
