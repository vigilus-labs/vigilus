"""Static list-price table for direct (non-OpenRouter) LLM providers.

OpenRouter publishes per-token prices over its API (see ``openrouter_pricing``).
Anthropic, OpenAI and Google do not, so cost for those providers is estimated
from this table of published list prices.

Prices are USD per 1M tokens, ``(input, output)``. The table is a dated
snapshot and will drift as vendors change pricing — operators can override or
extend it without a code change by writing ``<data_dir>/model_prices.json``:

    {"anthropic": {"claude-opus-5": [5.0, 25.0]}}

Entries in that file are merged over the defaults below, keyed by provider
type. Unknown models return ``None`` so the dashboard reports the cost as
incomplete rather than showing a wrong number.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

PRICES_SNAPSHOT_DATE = "2026-06-24"

# provider_type -> model id -> (USD per 1M input tokens, USD per 1M output tokens)
_DEFAULT_PRICES_PER_MTOK: dict[str, dict[str, tuple[float, float]]] = {
    "anthropic": {
        "claude-fable-5": (10.0, 50.0),
        "claude-mythos-5": (10.0, 50.0),
        "claude-opus-5": (5.0, 25.0),
        "claude-opus-4-8": (5.0, 25.0),
        "claude-opus-4-7": (5.0, 25.0),
        "claude-opus-4-6": (5.0, 25.0),
        "claude-opus-4-5": (5.0, 25.0),
        "claude-sonnet-5": (3.0, 15.0),
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-sonnet-4-5": (3.0, 15.0),
        "claude-haiku-4-5": (1.0, 5.0),
    },
    "openai": {
        "gpt-5": (1.25, 10.0),
        "gpt-5-mini": (0.25, 2.0),
        "gpt-5-nano": (0.05, 0.40),
        "gpt-4.1": (2.0, 8.0),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "gpt-4o": (2.50, 10.0),
        "gpt-4o-mini": (0.15, 0.60),
        "o3": (2.0, 8.0),
        "o4-mini": (1.10, 4.40),
    },
    "google": {
        "gemini-2.5-pro": (1.25, 10.0),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.5-flash-lite": (0.10, 0.40),
        "gemini-2.0-flash": (0.10, 0.40),
        "gemini-2.0-flash-lite": (0.075, 0.30),
    },
}

_DATE_SUFFIX = re.compile(r"-\d{6,8}$")

_cache: dict[str, dict[str, tuple[float, float]]] | None = None


def clear_price_cache() -> None:
    """Drop the merged table so the override file is re-read (tests, reloads)."""
    global _cache
    _cache = None


def _load_overrides() -> dict[str, dict[str, tuple[float, float]]]:
    """Read ``<data_dir>/model_prices.json``. Never raises — bad file is ignored."""
    try:
        from vigilus.config import get_settings

        path = Path(get_settings().data_dir) / "model_prices.json"
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text())
    except Exception as e:
        logger.warning("usage.model_prices_override_failed", error=str(e))
        return {}

    out: dict[str, dict[str, tuple[float, float]]] = {}
    if not isinstance(raw, dict):
        logger.warning("usage.model_prices_override_invalid", reason="not an object")
        return {}
    for provider, models in raw.items():
        if not isinstance(models, dict):
            continue
        table: dict[str, tuple[float, float]] = {}
        for model, pair in models.items():
            try:
                inp, outp = float(pair[0]), float(pair[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if math.isfinite(inp) and math.isfinite(outp) and inp >= 0 and outp >= 0:
                table[str(model).lower()] = (inp, outp)
        if table:
            out[str(provider).lower()] = table
    return out


def get_price_table() -> dict[str, dict[str, tuple[float, float]]]:
    """Return the merged (defaults + override file) price table, cached."""
    global _cache
    if _cache is not None:
        return _cache
    merged = {p: dict(models) for p, models in _DEFAULT_PRICES_PER_MTOK.items()}
    for provider, models in _load_overrides().items():
        merged.setdefault(provider, {}).update(models)
    _cache = merged
    return merged


def normalize_model(model: str) -> str:
    """Strip vendor prefix and dated snapshot suffix so aliases match.

    ``anthropic/claude-opus-5`` and ``claude-opus-5-20260101`` both normalize to
    ``claude-opus-5``. Also handles Bedrock's ``anthropic.`` prefix and Vertex's
    ``model@version`` form.
    """
    name = (model or "").strip().lower()
    if not name:
        return ""
    name = name.split("@", 1)[0]
    if "/" in name:
        name = name.rsplit("/", 1)[1]
    if name.startswith("anthropic."):
        name = name[len("anthropic.") :]
    if name.endswith(":latest"):
        name = name[: -len(":latest")]
    return _DATE_SUFFIX.sub("", name)


def lookup_price_per_mtok(
    provider_type: str | None,
    model: str | None,
    *,
    table: dict[str, dict[str, tuple[float, float]]] | None = None,
) -> tuple[float, float] | None:
    """Return ``(input, output)`` USD per 1M tokens, or None if unknown.

    Matching is exact on the normalized model id first, then falls back to the
    longest known model id that the normalized name starts with — so variant
    suffixes (``-fast``, ``-thinking``) still price against their base model.
    """
    prices = table if table is not None else get_price_table()
    ptype = (provider_type or "").lower()
    models = prices.get(ptype)
    if not models:
        return None

    name = normalize_model(model or "")
    if not name:
        return None
    if name in models:
        return models[name]

    best: str | None = None
    for known in models:
        if name.startswith(known) and (best is None or len(known) > len(best)):
            best = known
    return models[best] if best else None


def estimate_static_cost(
    provider_type: str | None,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    *,
    table: dict[str, dict[str, tuple[float, float]]] | None = None,
) -> float | None:
    """Estimate USD cost from published list prices, or None if unpriced."""
    pair = lookup_price_per_mtok(provider_type, model, table=table)
    if pair is None:
        return None
    inp, outp = pair
    if not math.isfinite(inp) or not math.isfinite(outp):
        return None
    if inp < 0 or outp < 0:
        return None
    return (input_tokens * inp / 1_000_000) + (output_tokens * outp / 1_000_000)
