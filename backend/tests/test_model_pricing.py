"""Static list-price table for direct (non-OpenRouter) providers."""

from __future__ import annotations

import json

import pytest

from vigilus.core import model_pricing


def setup_function():
    model_pricing.clear_price_cache()


def teardown_function():
    model_pricing.clear_price_cache()


def test_normalize_model_strips_prefix_and_date():
    assert model_pricing.normalize_model("anthropic/claude-opus-5") == "claude-opus-5"
    assert model_pricing.normalize_model("claude-opus-5-20260101") == "claude-opus-5"
    assert model_pricing.normalize_model("anthropic.claude-opus-5") == "claude-opus-5"
    assert model_pricing.normalize_model("claude-opus-4-5@20251101") == "claude-opus-4-5"
    assert model_pricing.normalize_model("  Claude-Opus-5  ") == "claude-opus-5"
    assert model_pricing.normalize_model("") == ""


def test_estimate_static_cost_anthropic():
    # claude-opus-5 is $5/$25 per 1M tokens.
    cost = model_pricing.estimate_static_cost(
        "anthropic", "claude-opus-5", 1_000_000, 200_000
    )
    assert cost == pytest.approx(5.0 + 5.0)


def test_estimate_static_cost_matches_longest_prefix():
    # A variant suffix falls back to its base model, and the *longest* match
    # wins so gpt-4o-mini never prices as gpt-4o.
    mini = model_pricing.estimate_static_cost("openai", "gpt-4o-mini-preview", 1_000_000, 0)
    base = model_pricing.estimate_static_cost("openai", "gpt-4o", 1_000_000, 0)
    assert mini == pytest.approx(0.15)
    assert base == pytest.approx(2.50)


def test_estimate_static_cost_unknown_returns_none():
    assert model_pricing.estimate_static_cost("anthropic", "not-a-model", 10, 10) is None
    assert model_pricing.estimate_static_cost("openrouter", "claude-opus-5", 10, 10) is None
    assert model_pricing.estimate_static_cost("openai", None, 10, 10) is None
    assert model_pricing.estimate_static_cost(None, "gpt-4o", 10, 10) is None


def test_override_file_extends_and_overrides(tmp_path, monkeypatch):
    prices = {
        "anthropic": {"claude-opus-5": [1.0, 2.0]},
        "openai_compat": {"my-local-model": [0.5, 1.5]},
    }
    (tmp_path / "model_prices.json").write_text(json.dumps(prices))

    from vigilus.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    model_pricing.clear_price_cache()

    assert model_pricing.estimate_static_cost(
        "anthropic", "claude-opus-5", 1_000_000, 0
    ) == pytest.approx(1.0)
    assert model_pricing.estimate_static_cost(
        "openai_compat", "my-local-model", 0, 1_000_000
    ) == pytest.approx(1.5)
    # Untouched defaults survive the merge.
    assert model_pricing.estimate_static_cost(
        "anthropic", "claude-haiku-4-5", 1_000_000, 0
    ) == pytest.approx(1.0)


def test_malformed_override_file_is_ignored(tmp_path, monkeypatch):
    (tmp_path / "model_prices.json").write_text("{ not json")

    from vigilus.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    model_pricing.clear_price_cache()

    # Falls back to defaults instead of raising.
    assert model_pricing.estimate_static_cost(
        "anthropic", "claude-opus-5", 1_000_000, 0
    ) == pytest.approx(5.0)
