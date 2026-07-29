# Token Usage Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist per-completion LLM token usage, estimate OpenRouter cost, and show rolling-window aggregates (Vigilus vs Operators) on a new Settings → Usage tab.

**Architecture:** After each non-stream `provider.complete()`, best-effort insert into a new `llm_usage` table via `record_llm_usage()`. OpenRouter prices come from a cached catalog lookup. `GET /api/usage?window=…` aggregates rows; the frontend Settings page adds a Usage tab that consumes it.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, React + Vite + TypeScript, existing Settings page patterns (`useState`/`useEffect` + `api` client — not TanStack Query in Settings).

**Spec:** `docs/superpowers/specs/2026-07-29-token-usage-dashboard-design.md`

## Global Constraints

- Metering is best-effort: never raise into the chat/operator loop; log `usage.record_failed` and continue.
- Missing/empty `response.usage` → skip insert (no zero rows for unknown).
- Tokens for all providers; `estimated_cost_usd` only when OpenRouter pricing resolves.
- Window bounds use `get_app_timezone()`; “today” = start of local calendar day.
- All new API routes gated by `require_user` (via `main.py` `auth_dep`).
- No secrets in usage rows or logs — only counts, model ids, actor ids.
- Follow existing model conventions: `String(36)` UUID PKs, `DateTime(timezone=True)`, `_uuid` / `_utcnow`.
- Do not capture streaming completions or compressor LLM calls in v1.
- Run backend commands from `backend/`; frontend from `frontend/`.
- Set `VIGILUS_SECRET` for tests (conftest already setdefaults).

## File Structure

| File | Responsibility |
|---|---|
| `backend/vigilus/db/models.py` | `UsageActorType` enum + `LlmUsage` model |
| `backend/vigilus/db/migrations/versions/2026_07_29_1200-<rev>_add_llm_usage.py` | Create `llm_usage` table (idempotent) |
| `backend/vigilus/core/openrouter_pricing.py` | In-process price cache + `estimate_cost_usd` |
| `backend/vigilus/core/llm_usage.py` | `record_llm_usage`, window cutoff, `get_usage_summary` |
| `backend/vigilus/schemas/usage.py` | Pydantic response models |
| `backend/vigilus/api/usage.py` | `GET /usage` |
| `backend/vigilus/main.py` | Register usage router |
| `backend/vigilus/api/chat.py` | Record after orchestrator `complete()` |
| `backend/vigilus/core/turn.py` | Pass provider metadata into `_run_orchestrator` |
| `backend/vigilus/core/operator_runtime.py` | Record after operator `complete()` |
| `backend/tests/test_openrouter_pricing.py` | Cost math + cache miss |
| `backend/tests/test_llm_usage.py` | Record + window + aggregation |
| `backend/tests/test_usage_api.py` | HTTP aggregates |
| `frontend/src/types/index.ts` | Usage response types |
| `frontend/src/lib/api.ts` | `getUsage(window)` |
| `frontend/src/pages/Settings/index.tsx` | Usage tab UI |

---

### Task 1: `LlmUsage` model + Alembic migration

**Files:**
- Modify: `backend/vigilus/db/models.py` (add enum near other enums; add model after `Action` or at end of models section)
- Create: `backend/vigilus/db/migrations/versions/2026_07_29_1200-a1b2c3d4e5f6_add_llm_usage.py`
- Test: `backend/tests/test_llm_usage.py` (create table via `create_all` fixture; insert round-trip)

**Interfaces:**
- Produces: `UsageActorType` (`orchestrator` \| `operator`), `LlmUsage` ORM model with columns from the spec
- Consumes: existing `_uuid`, `_utcnow`, `Base`

- [ ] **Step 1: Write the failing round-trip test**

Create `backend/tests/test_llm_usage.py`:

```python
"""Tests for LLM usage metering (model, record, aggregate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from vigilus.db.models import LlmUsage, UsageActorType


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_llm_usage.py::test_llm_usage_model_round_trip -v`

Expected: FAIL with `ImportError` / `cannot import name 'LlmUsage'`

- [ ] **Step 3: Add enum + model to `models.py`**

Near other enums (after `FindingSeverity`):

```python
class UsageActorType(str, enum.Enum):
    orchestrator = "orchestrator"
    operator = "operator"
```

At end of file (before or after `NetworkSegment`):

```python
class LlmUsage(Base):
    """Per-completion token usage attributed to Vigilus or an Operator."""

    __tablename__ = "llm_usage"

    id = Column(String(36), primary_key=True, default=_uuid)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    actor_type = Column(Enum(UsageActorType), nullable=False, index=True)
    operator_id = Column(String(36), ForeignKey("operators.id", ondelete="SET NULL"), nullable=True)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    provider_id = Column(String(36), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True)
    provider_type = Column(String(64), nullable=True, index=True)
    model = Column(String(255), nullable=True)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    estimated_cost_usd = Column(Float, nullable=True)

    __table_args__ = (
        # Composite indexes for common aggregate filters
        # (created via migration; declare here if using create_all in tests is enough with single-column indexes)
    )
```

Also import `Index` from sqlalchemy if adding composite indexes on the model; otherwise create composites only in the migration (preferred — keep model simple with the column `index=True` flags above).

- [ ] **Step 4: Add Alembic migration**

Create `backend/vigilus/db/migrations/versions/2026_07_29_1200-a1b2c3d4e5f6_add_llm_usage.py`:

- `revision = "a1b2c3d4e5f6"`
- `down_revision = "f8a5c36d0b23"` (current head)
- Idempotent `_has_table` guard like other migrations
- Create `llm_usage` with all columns matching the model
- Indexes: `ix_llm_usage_created_at`, `ix_llm_usage_actor_type`, `ix_llm_usage_provider_type`, plus composite `ix_llm_usage_actor_created` on `(actor_type, operator_id, created_at)`

```python
"""add llm_usage

Revision ID: a1b2c3d4e5f6
Revises: f8a5c36d0b23
Create Date: 2026-07-29 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f8a5c36d0b23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(bind, name: str) -> bool:
    from sqlalchemy import inspect as _inspect

    return name in _inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "llm_usage"):
        return
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "actor_type",
            sa.Enum("orchestrator", "operator", name="usageactortype"),
            nullable=False,
        ),
        sa.Column("operator_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("provider_id", sa.String(length=36), nullable=True),
        sa.Column("provider_type", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["operator_id"], ["operators.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.create_index("ix_llm_usage_actor_type", "llm_usage", ["actor_type"])
    op.create_index("ix_llm_usage_provider_type", "llm_usage", ["provider_type"])
    op.create_index(
        "ix_llm_usage_actor_created",
        "llm_usage",
        ["actor_type", "operator_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "llm_usage"):
        return
    op.drop_index("ix_llm_usage_actor_created", table_name="llm_usage")
    op.drop_index("ix_llm_usage_provider_type", table_name="llm_usage")
    op.drop_index("ix_llm_usage_actor_type", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
```

Note: Match enum creation style used by existing migrations in this repo if they differ (SQLite often stores enum as VARCHAR). If other migrations use `sa.String` for enums, prefer that for SQLite compatibility — check `channels` or `users` migrations and mirror.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_llm_usage.py::test_llm_usage_model_round_trip -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/vigilus/db/models.py \
  backend/vigilus/db/migrations/versions/2026_07_29_1200-a1b2c3d4e5f6_add_llm_usage.py \
  backend/tests/test_llm_usage.py
git commit -m "$(cat <<'EOF'
feat(usage): add llm_usage model and migration

Persist per-completion token counts for the Settings usage dashboard.
EOF
)"
```

---

### Task 2: OpenRouter pricing helper

**Files:**
- Create: `backend/vigilus/core/openrouter_pricing.py`
- Create: `backend/tests/test_openrouter_pricing.py`

**Interfaces:**
- Produces:
  - `async def get_openrouter_prices() -> dict[str, tuple[float, float]]` — model_id → `(prompt_per_token, completion_per_token)`
  - `def estimate_openrouter_cost(model: str, input_tokens: int, output_tokens: int, prices: dict[str, tuple[float, float]] | None = None) -> float | None`
  - `def clear_price_cache() -> None` (for tests)
- Consumes: `httpx` (same as `api/providers.py` openrouter_models), `structlog`

- [ ] **Step 1: Write failing tests**

```python
"""OpenRouter price cache and cost estimation."""

from __future__ import annotations

import pytest

from vigilus.core import openrouter_pricing as pricing


def setup_function():
    pricing.clear_price_cache()


def test_estimate_cost_basic():
    prices = {"openai/gpt-4o-mini": (0.00000015, 0.0000006)}
    cost = pricing.estimate_openrouter_cost(
        "openai/gpt-4o-mini", 1_000_000, 500_000, prices=prices
    )
    assert cost == pytest.approx(0.15 + 0.3)


def test_estimate_cost_unknown_model_returns_none():
    assert (
        pricing.estimate_openrouter_cost("missing/model", 10, 10, prices={}) is None
    )


def test_estimate_cost_bad_price_returns_none():
    assert (
        pricing.estimate_openrouter_cost("m", 10, 10, prices={"m": (float("nan"), 0.1)})
        is None
    )


@pytest.mark.asyncio
async def test_get_openrouter_prices_caches(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {
                        "id": "x/y",
                        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **k: FakeClient())

    p1 = await pricing.get_openrouter_prices()
    p2 = await pricing.get_openrouter_prices()
    assert calls["n"] == 1
    assert p1["x/y"] == (0.000001, 0.000002)
    assert p2 == p1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_openrouter_pricing.py -v`

Expected: FAIL import error

- [ ] **Step 3: Implement `openrouter_pricing.py`**

```python
"""OpenRouter list-price lookup for estimated LLM cost."""

from __future__ import annotations

import math
import time
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_CACHE: dict[str, tuple[float, float]] | None = None
_CACHE_AT: float = 0.0
_TTL_SECONDS = 6 * 3600  # 6 hours


def clear_price_cache() -> None:
    global _CACHE, _CACHE_AT
    _CACHE = None
    _CACHE_AT = 0.0


def estimate_openrouter_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    prices: dict[str, tuple[float, float]] | None = None,
) -> float | None:
    """Return estimated USD cost, or None if price unknown/unusable."""
    table = prices if prices is not None else _CACHE
    if not table or not model:
        return None
    pair = table.get(model)
    if not pair:
        return None
    prompt_p, completion_p = pair
    if not math.isfinite(prompt_p) or not math.isfinite(completion_p):
        return None
    if prompt_p < 0 or completion_p < 0:
        return None
    return (input_tokens * prompt_p) + (output_tokens * completion_p)


async def get_openrouter_prices() -> dict[str, tuple[float, float]]:
    """Fetch (or return cached) OpenRouter USD-per-token prices by model id."""
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    if _CACHE is not None and (now - _CACHE_AT) < _TTL_SECONDS:
        return _CACHE

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                timeout=15.0,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
    except Exception as e:
        logger.warning("usage.openrouter_prices_failed", error=str(e))
        return _CACHE or {}

    out: dict[str, tuple[float, float]] = {}
    for m in data.get("data", []):
        mid = m.get("id")
        pricing = m.get("pricing") or {}
        try:
            prompt = float(pricing.get("prompt", ""))
            completion = float(pricing.get("completion", ""))
        except (TypeError, ValueError):
            continue
        if mid and math.isfinite(prompt) and math.isfinite(completion):
            out[mid] = (prompt, completion)

    _CACHE = out
    _CACHE_AT = now
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_openrouter_pricing.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/vigilus/core/openrouter_pricing.py backend/tests/test_openrouter_pricing.py
git commit -m "$(cat <<'EOF'
feat(usage): add OpenRouter price cache for cost estimates
EOF
)"
```

---

### Task 3: `record_llm_usage` + window windows + aggregation

**Files:**
- Create: `backend/vigilus/core/llm_usage.py`
- Modify: `backend/tests/test_llm_usage.py` (append tests)

**Interfaces:**
- Produces:
  - `async def record_llm_usage(*, usage: dict[str, int], actor_type: UsageActorType | str, operator_id: str | None = None, session_id: str | None = None, provider_id: str | None = None, provider_type: str | None = None, model: str | None = None) -> None` — keyword-only; opens its own short-lived DB session via `get_session_factory()` and commits (never touches the caller’s transaction)
  - `def window_start(window: str, *, now: datetime | None = None, tz: ZoneInfo | None = None) -> datetime | None` — returns lower bound or `None` for `all`
  - `async def get_usage_summary(db, window: str) -> dict` — shape matching API response (totals, by_actor, by_provider, cost_incomplete)
- Consumes: `LlmUsage`, `Operator`, `get_app_timezone`, `get_openrouter_prices`, `estimate_openrouter_cost`

- [ ] **Step 1: Write failing tests** (append to `test_llm_usage.py`)

```python
from zoneinfo import ZoneInfo

from vigilus.core.llm_usage import get_usage_summary, record_llm_usage, window_start
from vigilus.db.models import Operator, PermissionLevel, Provider, ProviderType


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
    async def _prices():
        return {"m": (0.001, 0.002)}

    monkeypatch.setattr("vigilus.core.llm_usage.get_openrouter_prices", _prices)

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_llm_usage.py -v`

Expected: FAIL on missing `vigilus.core.llm_usage`

- [ ] **Step 3: Implement `core/llm_usage.py`**

Key behaviors:

1. `record_llm_usage`: skip when `usage` is empty/missing or both token counts parse to ≤0. Otherwise `int(usage.get("input_tokens") or 0)` / same for output.
2. For `provider_type == "openrouter"` (lowercased), await `get_openrouter_prices()` and `estimate_openrouter_cost`.
3. Wrap entire body in `try/except Exception`, log `usage.record_failed`, return. Always use a short-lived session from `get_session_factory()` (tests share the in-memory engine via conftest).

```python
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

        from vigilus.db.base import get_session_factory

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
```

4. `window_start`: validate window ∈ `{today,7d,30d,all}`; raise `ValueError` for unknown (API uses `Literal` → 422 before this runs).
5. `get_usage_summary`:
   - Query rows with `created_at >= start` when start set
   - Always include Vigilus/`orchestrator` actor row (zeros if none)
   - Operators: only those with usage; resolve `Operator.name` (fallback `"Unknown operator"`)
   - Sum costs only where not null; `cost_incomplete = any(row.estimated_cost_usd is None)` among rows in window (if no rows: `false`, totals cost `null`)
   - `by_provider`: group by `provider_type`; display names:

```python
_PROVIDER_NAMES = {
    "openrouter": "OpenRouter",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openai_compat": "OpenAI Compatible",
    "google": "Google",
    "custom": "Custom",
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_llm_usage.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/vigilus/core/llm_usage.py backend/tests/test_llm_usage.py
git commit -m "$(cat <<'EOF'
feat(usage): record completions and aggregate by actor/window
EOF
)"
```

---

### Task 4: Wire capture into orchestrator + OperatorRuntime

**Files:**
- Modify: `backend/vigilus/api/chat.py` (`_run_orchestrator` signature + call site after `complete`)
- Modify: `backend/vigilus/core/turn.py` (pass provider metadata)
- Modify: `backend/vigilus/core/operator_runtime.py` (store provider row fields; record after complete)
- Modify: `backend/tests/test_llm_usage.py` or `backend/tests/test_delegation.py` — prefer a focused unit-style test that mocks `complete` on a minimal runtime if heavy; otherwise add an integration-style test in `test_llm_usage.py` that calls `record` wiring indirectly via a thin helper. Minimal acceptable test: monkeypatch `OperatorRuntime` path by invoking the same call pattern used in production after a fake response.

**Interfaces:**
- Consumes: `record_llm_usage`
- Extends `_run_orchestrator(..., provider_id: str | None = None, provider_type: str | None = None, model: str | None = None)`
- `OperatorRuntime.__init__` stores `self._provider_id`, `self._provider_type`, `self._model` from provider row / operator override

- [ ] **Step 1: Write a failing wiring test**

Append to `test_llm_usage.py`:

```python
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
    op.provider = provider
    op.operator_tools = []

    class FakeProvider:
        default_model = "m"

        async def complete(self, **kwargs):
            return LLMResponse(
                content="done",
                usage={"input_tokens": 11, "output_tokens": 3},
            )

    async def fake_prices():
        return {"m": (0.0, 0.0)}

    monkeypatch.setattr("vigilus.core.llm_usage.get_openrouter_prices", fake_prices)

    runtime = OperatorRuntime(op, fallback_provider=provider)
    runtime.provider = FakeProvider()

    async def _nop_tools(self):
        return []

    monkeypatch.setattr(OperatorRuntime, "_get_tools", _nop_tools)
    monkeypatch.setattr(
        OperatorRuntime, "_build_system_prompt", lambda self, tools: None
    )

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_llm_usage.py::test_operator_runtime_records_usage -v`

Expected: FAIL (no usage row)

- [ ] **Step 3: Wire `OperatorRuntime`**

In `__init__`, after building provider:

```python
self._provider_id = provider_row.id
self._provider_type = (
    provider_row.type.value if hasattr(provider_row.type, "value") else str(provider_row.type)
)
self._model = operator.model or provider_row.default_model
```

After successful `complete()` (before processing tool uses):

```python
from vigilus.core.llm_usage import record_llm_usage
from vigilus.db.models import UsageActorType

await record_llm_usage(
    usage=response.usage or {},
    actor_type=UsageActorType.operator,
    operator_id=self.operator.id,
    session_id=session_id,
    provider_id=self._provider_id,
    provider_type=self._provider_type,
    model=getattr(self.provider, "default_model", None) or self._model,
)
```

- [ ] **Step 4: Wire `_run_orchestrator` + callers**

Change signature:

```python
async def _run_orchestrator(
    llm_history: list[LLMMessage],
    provider: Any,
    system_prompt: str,
    *,
    db: AsyncSession,
    session_id: str | None = None,
    provider_id: str | None = None,
    provider_type: str | None = None,
    model: str | None = None,
    max_delegations: int = 5,
    bridge: StreamBridge | None = None,
    cancel_event: Any | None = None,
    unattended: bool = False,
) -> list[dict[str, Any]]:
```

After successful `complete()`:

```python
from vigilus.core.llm_usage import record_llm_usage
from vigilus.db.models import UsageActorType

await record_llm_usage(
    usage=response.usage or {},
    actor_type=UsageActorType.orchestrator,
    session_id=session_id,
    provider_id=provider_id,
    provider_type=provider_type,
    model=model or getattr(provider, "default_model", None),
)
```

In `send_message` call site (~L803):

```python
new_msgs = await _run_orchestrator(
    llm_history,
    provider,
    system_prompt,
    db=db,
    session_id=session.id,
    provider_id=provider_row.id,
    provider_type=provider_row.type.value,
    model=model,
    bridge=bridge,
    cancel_event=running_task.cancel_event,
)
```

In `core/turn.py`:

```python
provider, provider_row, model = await resolve_orchestrator_provider(db)
...
new_msgs = await _run_orchestrator(
    llm_history,
    provider,
    system_prompt,
    db=db,
    session_id=session.id,
    provider_id=provider_row.id,
    provider_type=provider_row.type.value,
    model=model,
    bridge=bridge,
    cancel_event=cancel_event,
    unattended=unattended,
)
```

- [ ] **Step 5: Run tests**

Run: `cd backend && pytest tests/test_llm_usage.py -v && pytest tests/test_chat.py tests/test_delegation.py -v --tb=line -q`

Expected: PASS (fix any signature mismatches in other `_run_orchestrator` callers — grep the repo)

- [ ] **Step 6: Commit**

```bash
git add backend/vigilus/api/chat.py backend/vigilus/core/turn.py \
  backend/vigilus/core/operator_runtime.py backend/vigilus/core/llm_usage.py \
  backend/tests/test_llm_usage.py
git commit -m "$(cat <<'EOF'
feat(usage): record tokens from orchestrator and operator loops
EOF
)"
```

---

### Task 5: Usage API

**Files:**
- Create: `backend/vigilus/schemas/usage.py`
- Create: `backend/vigilus/api/usage.py`
- Modify: `backend/vigilus/main.py` (import + `include_router`)
- Create: `backend/tests/test_usage_api.py`

**Interfaces:**
- Produces: `GET /api/usage?window=today|7d|30d|all` → `UsageSummaryResponse`
- Consumes: `get_usage_summary`

- [ ] **Step 1: Write failing API tests**

```python
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
    async def fake_prices():
        return {}

    monkeypatch.setattr("vigilus.core.llm_usage.get_openrouter_prices", fake_prices)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_usage_api.py -v`

Expected: 404 on `/api/usage`

- [ ] **Step 3: Add schemas + router + register**

`schemas/usage.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UsageTotals(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None


class UsageByActor(UsageTotals):
    actor_type: str
    operator_id: str | None
    name: str


class UsageByProvider(UsageTotals):
    provider_type: str | None
    provider_id: str | None = None
    name: str


class UsageSummaryResponse(BaseModel):
    window: str
    cost_incomplete: bool
    totals: UsageTotals
    by_actor: list[UsageByActor]
    by_provider: list[UsageByProvider]
```

`api/usage.py`:

```python
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.llm_usage import get_usage_summary
from vigilus.db.base import get_db
from vigilus.schemas.usage import UsageSummaryResponse

router = APIRouter(prefix="/usage", tags=["Usage"])

Window = Literal["today", "7d", "30d", "all"]


@router.get("", response_model=UsageSummaryResponse)
async def get_usage(
    window: Window = Query("7d"),
    db: AsyncSession = Depends(get_db),
):
    data = await get_usage_summary(db, window)
    return UsageSummaryResponse.model_validate(data)
```

In `main.py`, add import and:

```python
from vigilus.api.usage import router as usage_router
...
app.include_router(usage_router, prefix="/api", dependencies=auth_dep)
```

Ensure `get_usage_summary` returns dicts compatible with the schema (`total_tokens = input + output` on every bucket).

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_usage_api.py tests/test_llm_usage.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/vigilus/schemas/usage.py backend/vigilus/api/usage.py \
  backend/vigilus/main.py backend/tests/test_usage_api.py
git commit -m "$(cat <<'EOF'
feat(usage): add GET /api/usage aggregate endpoint
EOF
)"
```

---

### Task 6: Settings → Usage tab (frontend)

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/Settings/index.tsx`

**Interfaces:**
- Consumes: `GET /api/usage`
- Produces: Usage tab with window toggle, summary, by-actor table, by-provider section

- [ ] **Step 1: Add types**

In `frontend/src/types/index.ts`:

```typescript
export type UsageWindow = 'today' | '7d' | '30d' | 'all';

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
}

export interface UsageByActor extends UsageTotals {
  actor_type: 'orchestrator' | 'operator' | string;
  operator_id: string | null;
  name: string;
}

export interface UsageByProvider extends UsageTotals {
  provider_type: string | null;
  provider_id?: string | null;
  name: string;
}

export interface UsageSummary {
  window: UsageWindow | string;
  cost_incomplete: boolean;
  totals: UsageTotals;
  by_actor: UsageByActor[];
  by_provider: UsageByProvider[];
}
```

- [ ] **Step 2: Add API client method**

In `api.ts`, import `UsageSummary`, `UsageWindow` from types, add:

```typescript
  async getUsage(window: UsageWindow = '7d'): Promise<UsageSummary> {
    return this.get<UsageSummary>(`/usage?window=${encodeURIComponent(window)}`);
  }
```

- [ ] **Step 3: Add `UsageTab` + nav entry**

In `Settings/index.tsx`:

1. Import `BarChart3` (or `Activity`) from `lucide-react` and types `UsageSummary`, `UsageWindow`.
2. Add `UsageTab` function component **before** the default `Settings` export (near other tabs):

```tsx
function formatTokens(n: number): string {
  return n.toLocaleString();
}

function formatCost(usd: number | null | undefined): string {
  if (usd == null) return '—';
  if (usd < 0.01 && usd > 0) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

function UsageTab() {
  const [window, setWindow] = useState<UsageWindow>('7d');
  const [data, setData] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const summary = await api.getUsage(window);
        if (!cancelled) setData(summary);
      } catch (err: any) {
        if (!cancelled) setError(err?.message || 'Failed to load usage');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [window]);

  const windows: { id: UsageWindow; label: string }[] = [
    { id: 'today', label: 'Today' },
    { id: '7d', label: '7d' },
    { id: '30d', label: '30d' },
    { id: 'all', label: 'All' },
  ];

  if (loading && !data) {
    return <p className="text-sm text-text-secondary">Loading usage…</p>;
  }
  if (error) {
    return <p className="text-sm text-danger">{error}</p>;
  }
  if (!data) return null;

  const empty = data.totals.total_tokens === 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        {windows.map((w) => (
          <button
            key={w.id}
            type="button"
            onClick={() => setWindow(w.id)}
            className={`px-3 py-1.5 text-xs rounded-md border transition-colors ${
              window === w.id
                ? 'bg-accent/10 text-accent border-accent/30 font-medium'
                : 'border-border text-text-secondary hover:bg-surface'
            }`}
          >
            {w.label}
          </button>
        ))}
      </div>

      {empty ? (
        <p className="text-sm text-text-secondary">
          No usage recorded yet — send a chat to start metering.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-[12px] text-text-secondary mb-1">Total tokens</p>
              <p className="text-xl font-medium text-text-primary">
                {formatTokens(data.totals.total_tokens)}
              </p>
              <p className="text-[11px] text-text-secondary mt-1">
                {formatTokens(data.totals.input_tokens)} in · {formatTokens(data.totals.output_tokens)} out
              </p>
            </div>
            <div>
              <p className="text-[12px] text-text-secondary mb-1">Estimated cost</p>
              <p className="text-xl font-medium text-text-primary">
                {formatCost(data.totals.estimated_cost_usd)}
              </p>
              <p className="text-[11px] text-text-secondary mt-1">
                OpenRouter list prices
                {data.cost_incomplete ? ' · partial (some providers unpriced)' : ''}
              </p>
            </div>
          </div>

          <div>
            <h3 className="text-[13px] font-medium text-text-primary mb-3">By actor</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[12px] text-text-secondary border-b border-border">
                    <th className="py-2 pr-3 font-medium">Actor</th>
                    <th className="py-2 pr-3 font-medium">Input</th>
                    <th className="py-2 pr-3 font-medium">Output</th>
                    <th className="py-2 pr-3 font-medium">Total</th>
                    <th className="py-2 font-medium">Est. cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_actor.map((row) => (
                    <tr key={`${row.actor_type}-${row.operator_id ?? 'orch'}`} className="border-b border-border/60">
                      <td className="py-2 pr-3 text-text-primary">{row.name}</td>
                      <td className="py-2 pr-3 text-text-secondary">{formatTokens(row.input_tokens)}</td>
                      <td className="py-2 pr-3 text-text-secondary">{formatTokens(row.output_tokens)}</td>
                      <td className="py-2 pr-3 text-text-primary">{formatTokens(row.total_tokens)}</td>
                      <td className="py-2 text-text-secondary">{formatCost(row.estimated_cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {data.by_provider.length > 0 && (
            <div>
              <h3 className="text-[13px] font-medium text-text-primary mb-3">By provider</h3>
              <div className="space-y-2">
                {data.by_provider.map((row) => (
                  <div
                    key={row.provider_type ?? row.name}
                    className="flex items-center justify-between text-sm py-1"
                  >
                    <span className="text-text-primary">{row.name}</span>
                    <span className="text-text-secondary">
                      {formatTokens(row.total_tokens)} · {formatCost(row.estimated_cost_usd)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
```

3. In the sidebar nav, insert **Usage** after **LLM Providers** (matches “near model selection”):

```tsx
<button
  onClick={() => setActiveTab('usage')}
  className={`w-full flex items-center px-3 py-2 text-sm rounded-md transition-colors ${
    activeTab === 'usage'
      ? 'bg-accent/10 text-accent font-medium'
      : 'text-text-secondary hover:bg-surface hover:text-text-primary'
  }`}
>
  <BarChart3 className="w-4 h-4 mr-3" />
  Usage
</button>
```

4. Wire title + content:

```tsx
{activeTab === 'usage' && 'Usage'}
...
{activeTab === 'usage' && <UsageTab />}
```

- [ ] **Step 4: Typecheck / lint**

Run: `cd frontend && npm run build`

Expected: succeed (or at least `tsc -b` clean for these files)

- [ ] **Step 5: Manual smoke (optional but recommended)**

1. `vigilus init` / migrate, start backend + frontend
2. Open Settings → Usage → empty state
3. Send a chat via OpenRouter → refresh Usage → Vigilus tokens + non-null cost when priced
4. Trigger an operator delegation → Operator row appears

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api.ts \
  frontend/src/pages/Settings/index.tsx
git commit -m "$(cat <<'EOF'
feat(usage): add Settings Usage tab for token and cost aggregates
EOF
)"
```

---

### Task 7: Final verification

**Files:** none new

- [ ] **Step 1: Run backend suite subset + lint**

```bash
cd backend
pytest tests/test_llm_usage.py tests/test_openrouter_pricing.py tests/test_usage_api.py tests/test_chat.py tests/test_delegation.py -v
ruff check vigilus/core/llm_usage.py vigilus/core/openrouter_pricing.py vigilus/api/usage.py vigilus/schemas/usage.py
```

Expected: all PASS; ruff clean

- [ ] **Step 2: Run frontend build**

```bash
cd frontend && npm run build
```

Expected: success

- [ ] **Step 3: Confirm migration head**

```bash
cd backend && python -c "from vigilus.core.preflight import alembic_heads; print(alembic_heads())"
```

Expected: includes `a1b2c3d4e5f6` (or whatever revision id was used)

- [ ] **Step 4: No commit unless uncommitted fixes remain** — if fixes were needed, commit them with a clear message:

```bash
git commit -m "$(cat <<'EOF'
fix(usage): address verification findings
EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|---|---|
| `llm_usage` table + migration | 1 |
| OpenRouter cost estimation + cache | 2 |
| Best-effort `record_llm_usage` | 3 |
| Window Today/7d/30d/All via `get_app_timezone` | 3 |
| Aggregation + `cost_incomplete` | 3 |
| Orchestrator + Operator capture | 4 |
| `GET /api/usage` + auth | 5 |
| Settings Usage tab UI | 6 |
| Tokens all providers; $ OpenRouter only | 2–3 |
| Empty state copy | 6 |
| Tests (unit + API + wiring) | 1–5, 7 |
| No streaming / compressor metering | explicit non-goal (no task) |

## Plan self-review notes

- `record_llm_usage` is keyword-only and uses its own DB session (Task 3/4) so chat transactions are never poisoned.
- Revision id `a1b2c3d4e5f6` is a placeholder-style id — if the implementer generates a real alembic rev, update `down_revision` chain and Task 7 check accordingly; keep `down_revision = f8a5c36d0b23` unless head moved.
- Enum column on SQLite: if migration fails, switch `actor_type` to `sa.String(32)` to match other flexible migrations in this repo.
- Do not meter `ContextCompressor` completions in v1.
