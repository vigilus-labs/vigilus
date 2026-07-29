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
