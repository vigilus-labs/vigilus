"""Robustness tests for LLM provider error handling and retry.

Covers Bug 1: OpenAI-compatible gateways that return HTTP 200 with an in-body
error and choices=None must surface as a typed, retryable ProviderError
instead of crashing with a TypeError from subscripting None.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import vigilus.providers.base as base_mod
from vigilus.providers.base import LLMMessage, ProviderError, retry_transient
from vigilus.providers.openai_provider import OpenAIProvider


def _error_response(code, message):
    """Simulate an OpenAI-compat gateway's in-body error (HTTP 200, no choices)."""
    resp = MagicMock()
    resp.error = {"code": code, "message": message}
    resp.choices = None
    return resp


# ── retry_transient() unit tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr(base_mod.asyncio, "sleep", AsyncMock(return_value=None))
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("timeout", status_code=504)  # transient
        return "ok"

    assert await retry_transient(flaky, max_attempts=5) == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_retry_does_not_retry_non_transient():
    calls = {"n": 0}

    async def bad():
        calls["n"] += 1
        raise ProviderError("bad request", status_code=400)  # non-transient

    with pytest.raises(ProviderError):
        await retry_transient(bad, max_attempts=5)
    assert calls["n"] == 1  # raised immediately, no retry budget spent


@pytest.mark.asyncio
async def test_retry_exhausts_attempts_on_persistent_transient(monkeypatch):
    monkeypatch.setattr(base_mod.asyncio, "sleep", AsyncMock(return_value=None))
    calls = {"n": 0}

    async def always_fail():
        calls["n"] += 1
        raise ProviderError("down", status_code=503)

    with pytest.raises(ProviderError):
        await retry_transient(always_fail, max_attempts=3)
    assert calls["n"] == 3


def test_provider_error_infers_transience_from_code():
    assert ProviderError("x", status_code=504).transient is True
    assert ProviderError("x", status_code=429).transient is True
    assert ProviderError("x", status_code=400).transient is False
    assert ProviderError("x").transient is False  # no code → not transient


# ── OpenAIProvider.complete() guard tests ────────────────────────────────


@pytest.mark.asyncio
async def test_complete_raises_provider_error_on_choices_none(monkeypatch):
    """The reported crash: gateway returns 504 in-body → choices is None."""
    monkeypatch.setattr(base_mod.asyncio, "sleep", AsyncMock(return_value=None))

    provider = OpenAIProvider(api_key="sk-test", default_model="gpt-4o")
    provider.client = MagicMock()
    provider.client.chat.completions.create = AsyncMock(
        return_value=_error_response(504, "upstream timeout")
    )

    with pytest.raises(ProviderError) as ei:
        await provider.complete(messages=[LLMMessage(role="user", content="hi")], system="x")

    assert ei.value.status_code == 504
    assert ei.value.transient is True
    assert "504" in str(ei.value)
    assert provider.client.chat.completions.create.await_count == 3  # retried


@pytest.mark.asyncio
async def test_complete_non_transient_error_is_not_retried(monkeypatch):
    slept = []

    async def record_sleep(d):
        slept.append(d)

    monkeypatch.setattr(base_mod.asyncio, "sleep", record_sleep)

    provider = OpenAIProvider(api_key="sk-test", default_model="gpt-4o")
    provider.client = MagicMock()
    create = AsyncMock(return_value=_error_response(400, "bad request"))
    provider.client.chat.completions.create = create

    with pytest.raises(ProviderError) as ei:
        await provider.complete(messages=[LLMMessage(role="user", content="hi")], system="x")

    assert ei.value.status_code == 400
    assert ei.value.transient is False
    assert create.await_count == 1
    assert slept == []  # never slept → no retry
