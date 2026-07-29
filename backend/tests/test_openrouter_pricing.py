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
