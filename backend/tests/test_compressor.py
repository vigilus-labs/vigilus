"""Tests for context compression."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from vigilus.core.compressor import (
    ContextCompressor,
    estimate_tokens,
    _split_messages,
    _CHARS_PER_TOKEN,
    _MIN_RECENT_MESSAGES,
)
from vigilus.providers.base import LLMMessage, LLMResponse


def test_estimate_tokens_empty():
    """Empty message list has ~0 tokens."""
    assert estimate_tokens([]) == 0


def test_estimate_tokens_basic():
    """Estimate tokens from string content."""
    msgs = [
        LLMMessage(role="user", content="Hello, this is a test message."),
        LLMMessage(role="assistant", content="I received your message."),
    ]
    tokens = estimate_tokens(msgs)
    # Each message has content chars + 20 overhead
    expected_chars = len("Hello, this is a test message.") + 20 + len("I received your message.") + 20
    assert tokens == expected_chars // _CHARS_PER_TOKEN
    assert tokens > 0


def test_split_messages_short_list():
    """Message list shorter than keep_recent returns empty older."""
    msgs = [
        LLMMessage(role="user", content="msg1"),
        LLMMessage(role="assistant", content="msg2"),
    ]
    older, recent = _split_messages(msgs, keep_recent=6)
    assert len(older) == 0
    assert len(recent) == 2


def test_split_messages_long_list():
    """Long message list splits correctly."""
    msgs = [LLMMessage(role="user", content=f"msg{i}") for i in range(20)]
    older, recent = _split_messages(msgs, keep_recent=6)
    assert len(older) == 14
    assert len(recent) == 6


@pytest.mark.asyncio
async def test_compressor_skip_when_below_threshold():
    """Compressor skips compression when below threshold."""
    provider = AsyncMock()
    # Very short messages — well below any threshold
    msgs = [
        LLMMessage(role="user", content="Hi"),
        LLMMessage(role="assistant", content="Hello"),
    ]
    compressor = ContextCompressor(provider=provider, max_tokens=100_000)
    result, summary = await compressor.compress_if_needed(msgs)

    assert result is msgs  # Same object — no compression
    assert summary is None
    provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_compressor_triggers_when_above_threshold():
    """Compressor triggers when above threshold."""
    provider = AsyncMock()
    provider.complete.return_value = LLMResponse(
        content="Summary: User asked about security. Found 2 vulnerabilities.",
        tool_uses=[],
    )

    # Create many long messages to exceed a low threshold
    msgs = []
    for i in range(30):
        msgs.append(LLMMessage(
            role="user",
            content="x" * 500 + f" message {i}",
        ))
        msgs.append(LLMMessage(
            role="assistant",
            content="y" * 500 + f" response {i}",
        ))

    compressor = ContextCompressor(provider=provider, max_tokens=1000, trigger_threshold=0.7)
    result, summary = await compressor.compress_if_needed(msgs)

    assert summary is not None
    assert len(result) < len(msgs)
    # First message should be the summary
    assert "CONTEXT SUMMARY" in result[0].content
    provider.complete.assert_called_once()


@pytest.mark.asyncio
async def test_compressor_fallback_on_provider_error():
    """Compressor falls back gracefully when provider fails."""
    provider = AsyncMock()
    provider.complete.side_effect = RuntimeError("Provider unavailable")

    msgs = [LLMMessage(role="user", content=f"msg{i} " * 50) for i in range(15)]

    compressor = ContextCompressor(provider=provider, max_tokens=500, trigger_threshold=0.5)
    result, summary = await compressor.compress_if_needed(msgs)

    # Should still produce a result (fallback summary)
    assert summary is not None
    assert len(result) < len(msgs)


def test_estimate_tokens_with_dict_content():
    """Estimate tokens handles dict/list content."""
    msgs = [
        LLMMessage(role="user", content="short"),
        LLMMessage(role="assistant", content={"key": "value", "nested": {"a": 1}}),
    ]
    tokens = estimate_tokens(msgs)
    assert tokens > 0
