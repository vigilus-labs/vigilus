# Token Usage Dashboard — Design Spec

**Date:** 2026-07-29  
**Status:** Approved for planning  
**Scope:** Settings Usage tab with token + estimated-cost aggregates; OpenRouter cost first

## Problem

Vigilus already receives normalized token counts on every non-stream LLM completion (`LLMResponse.usage`: `input_tokens`, `output_tokens`), including OpenRouter via the OpenAI-compat provider. Those values are discarded. Operators and the orchestrator share providers with no visibility into who is consuming tokens or what it costs.

## Goals (v1)

- Persist per-completion usage attributed to **Vigilus (orchestrator)** vs each **Operator**.
- Show aggregates in Settings under a new **Usage** tab with windows: **Today / 7d / 30d / All**.
- Display **tokens** (input, output, total) and **estimated USD cost** where price data exists.
- Record tokens for **all providers** from day one; estimate **$ only for OpenRouter** initially (others show “—” for cost).
- Metering must never break chat or tool execution (best-effort writes).

## Non-goals (v1)

- Date-range pickers, charts, CSV export, spend caps / budgets
- Per-session or per-call drill-down UI
- Cost catalogs for Anthropic / OpenAI / Google / generic OpenAI-compat (follow-up)
- Streaming usage capture (stream paths currently omit usage; out of scope until providers expose final usage)

## Decisions

| Topic | Choice |
|---|---|
| Metrics | Tokens + estimated cost (OpenRouter pricing) |
| History UI | Rolling windows only (Today / 7d / 30d / All); no drill-down |
| Placement | New Settings **Usage** tab |
| Provider coverage | Tokens for all; cost for OpenRouter only in v1 |
| Storage | Dedicated `llm_usage` table (not Action audit piggyback, not counter-only) |

## Architecture

```
provider.complete() → LLMResponse.usage
        ↓
  record_llm_usage()  (best-effort)
        ↓
     llm_usage rows
        ↓
  GET /api/usage?window=…
        ↓
  Settings → Usage tab
```

Capture points (existing call sites that already discard usage):

1. Orchestrator chat loop (`api/chat.py`) — `actor_type=orchestrator`
2. Operator agent loop (`core/operator_runtime.py`) — `actor_type=operator` + `operator_id`

Shared turn helpers (e.g. `core/turn.py`) should route through the same recorder if they invoke completions independently; prefer one helper used by both paths.

## Data model

### Table `llm_usage`

| Column | Type | Notes |
|---|---|---|
| `id` | `String(36)` PK | UUID via existing `_uuid` helper |
| `created_at` | `DateTime(timezone=True)` | Indexed; used for window filters |
| `actor_type` | string/enum | `orchestrator` \| `operator` |
| `operator_id` | `String(36)` FK nullable | Required when actor is operator; FK → `operators.id` |
| `session_id` | `String(36)` FK nullable | Chat session when available; FK → `sessions.id` |
| `provider_id` | `String(36)` FK nullable | Provider config used; FK → `providers.id` |
| `provider_type` | string | Snapshot (e.g. `openrouter`) for stable reporting if provider deleted |
| `model` | string | Snapshot of model id |
| `input_tokens` | int | From `LLMResponse.usage` |
| `output_tokens` | int | From `LLMResponse.usage` |
| `estimated_cost_usd` | float nullable | Null when unknown |

Indexes: `(created_at)`, `(actor_type, operator_id, created_at)`, `(provider_type, created_at)`.

Alembic migration required; seed unchanged.

## Cost estimation (OpenRouter)

- When `provider_type == openrouter`, resolve prompt/completion unit prices from the OpenRouter models catalog (same source as `GET /providers/openrouter/models`; each model’s `pricing.prompt` / `pricing.completion` are USD-per-token strings).
- Cache prices in-process (TTL on the order of hours; refresh on miss).
- `estimated_cost_usd = input_tokens * float(prompt) + output_tokens * float(completion)`.
- Price miss, unparseable price, or non-OpenRouter → store tokens, leave `estimated_cost_usd` null.
- UI copy: cost is **estimated** from OpenRouter list prices, not invoices.

## API

`GET /api/usage?window=today|7d|30d|all`

- Gated by `require_user` (same as other Settings APIs).
- Window boundaries use `get_app_timezone()` (orchestrator config / General settings). “Today” = start of local calendar day in that timezone through now; `7d`/`30d` = rolling lookback from now; `all` = no lower bound.

Response:

```json
{
  "window": "7d",
  "cost_incomplete": false,
  "totals": {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "estimated_cost_usd": null
  },
  "by_actor": [
    {
      "actor_type": "orchestrator",
      "operator_id": null,
      "name": "Vigilus",
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0,
      "estimated_cost_usd": null
    },
    {
      "actor_type": "operator",
      "operator_id": "...",
      "name": "Operator Name",
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0,
      "estimated_cost_usd": null
    }
  ],
  "by_provider": [
    {
      "provider_type": "openrouter",
      "provider_id": null,
      "name": "OpenRouter",
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0,
      "estimated_cost_usd": null
    }
  ]
}
```

Aggregation rules:

- Sum tokens in window; sum cost only over non-null cost rows. If any contributing row has null cost, totals/`estimated_cost_usd` may still be a partial sum — expose a boolean `cost_incomplete: true` when any row in the aggregate lacks cost, so the UI can show a subtle “partial” hint.
- Include orchestrator row even when zero if we want a stable layout; operators with zero usage in the window may be omitted (prefer omit to keep the list short).
- `by_provider` groups by `provider_type` (and name from catalog/type label).

## UI — Settings → Usage

- New tab next to Providers / Credentials / General / …
- Window toggle: Today | 7d | 30d | All
- Summary strip: total tokens + estimated cost (note: OpenRouter-estimated)
- Primary table: Vigilus + Operators — input, output, total, est. cost (“—” when null)
- Secondary: by-provider breakdown (collapsible or compact)
- Empty state when no rows in window: “No usage recorded yet — send a chat to start metering.”
- Match existing Settings patterns (TanStack Query + `lib/api.ts`); no new design system

## Error handling

- Recorder wraps DB/pricing in try/except: log (`structlog` event-name-first, e.g. `usage.record_failed`) and return; never raise into the chat loop.
- Missing or empty `response.usage` → skip insert (do not write zero rows for “unknown”).
- API failures → standard HTTP errors; UI shows existing error patterns.

## Testing

- Unit: OpenRouter cost math; window cutoff boundaries; aggregation with mixed null costs → `cost_incomplete`.
- Integration: mocked `complete()` with usage → row written; orchestrator vs operator attribution; `GET /api/usage` aggregates seeded data.
- Manual: Usage tab empty + after OpenRouter chat; non-OpenRouter shows tokens and “—” cost.

## Rollout / follow-ups

1. Migration + model + `record_llm_usage` helper  
2. Wire orchestrator + `OperatorRuntime`  
3. OpenRouter pricing helper + usage API  
4. Settings Usage tab  

**Later:** Anthropic/OpenAI/Google pricing; streaming final-usage; optional per-session drill-down.

## Security

- Usage API requires authenticated user; no secrets in usage rows.
- Do not log API keys or full prompts in usage events — only counts, model ids, actor ids.
- Fail-open for metering only (availability); fail-closed remains for RBAC/tool policy paths (unchanged).
