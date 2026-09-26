"""Tests for context compression."""

from unittest.mock import AsyncMock

import pytest

from vigilus.core.compressor import (
    _CHARS_PER_TOKEN,
    ContextCompressor,
    _split_messages,
    estimate_tokens,
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
    expected_chars = (
        len("Hello, this is a test message.") + 20 + len("I received your message.") + 20
    )
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
        msgs.append(
            LLMMessage(
                role="user",
                content="x" * 500 + f" message {i}",
            )
        )
        msgs.append(
            LLMMessage(
                role="assistant",
                content="y" * 500 + f" response {i}",
            )
        )

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


def test_resolve_context_window_priority():
    """Explicit override beats a known model id, which beats the type default."""
    from types import SimpleNamespace

    from vigilus.core.compressor import resolve_context_window
    from vigilus.db.models import ProviderType

    explicit = SimpleNamespace(
        context_window=4_096, default_model="claude-opus-4-8", type=ProviderType.anthropic
    )
    assert resolve_context_window(explicit, "claude-opus-4-8") == 4_096

    claude = SimpleNamespace(context_window=None, default_model=None, type=ProviderType.anthropic)
    assert resolve_context_window(claude, "anthropic/claude-sonnet-4") == 200_000

    local = SimpleNamespace(
        context_window=None, default_model="llama3.1", type=ProviderType.openai_compat
    )
    assert resolve_context_window(local, "llama3.1") == 8_192

    unknown = SimpleNamespace(context_window=None, default_model=None, type=ProviderType.openai)
    assert resolve_context_window(unknown, "some-new-model") == 100_000


def test_elide_keeps_recent_tool_results_and_ids():
    from vigilus.core.compressor import _TOOL_RESULT_STUB, elide_old_tool_results

    messages = [LLMMessage(role="user", content="go")]
    for i in range(6):
        messages.append(LLMMessage(role="assistant", content="", tool_calls=[{"id": f"c{i}"}]))
        messages.append(
            LLMMessage(role="tool", content=f"body-{i}-" + ("x" * 40), tool_use_id=f"c{i}")
        )
    elided = elide_old_tool_results(messages)
    tool_bodies = [m.content for m in elided if m.role == "tool"]
    assert tool_bodies[:2] == [_TOOL_RESULT_STUB, _TOOL_RESULT_STUB]
    assert tool_bodies[2].startswith("body-2")
    assert elided[2].tool_use_id == "c0"
    # Nothing to change returns the same list.
    assert elide_old_tool_results(elided) is elided


def test_split_does_not_start_recent_on_a_tool_message():
    messages = [
        LLMMessage(role="user", content="a"),
        LLMMessage(role="assistant", content="", tool_calls=[{"id": "t1"}]),
        LLMMessage(role="tool", content="result", tool_use_id="t1"),
        LLMMessage(role="assistant", content="done"),
    ]
    older, recent = _split_messages(messages, keep_recent=2)
    assert recent[0].role != "tool"
    assert recent[0].tool_calls
    assert older[-1].role == "user"


@pytest.mark.asyncio
async def test_small_window_compresses_and_large_window_does_not():
    provider = AsyncMock()
    provider.complete.return_value = LLMResponse(content="Summary of the long run.", tool_uses=[])
    msgs = [LLMMessage(role="user", content="x" * 2000) for _ in range(20)]

    small = ContextCompressor(provider=provider, max_tokens=8_192)
    compressed, summary = await small.compress_if_needed(msgs)
    assert summary is not None
    assert len(compressed) < len(msgs)

    provider.complete.reset_mock()
    large = ContextCompressor(provider=provider, max_tokens=200_000)
    unchanged, summary = await large.compress_if_needed(msgs)
    assert unchanged is msgs
    assert summary is None
    provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_exact_count_can_cancel_a_high_heuristic():
    """A provider count under the threshold wins over the character estimate."""
    provider = AsyncMock()
    provider.count_tokens.return_value = 10
    msgs = [LLMMessage(role="user", content="x" * 2000) for _ in range(20)]
    compressor = ContextCompressor(provider=provider, max_tokens=8_192)
    result, summary = await compressor.compress_if_needed(msgs)
    assert result is msgs
    assert summary is None
    provider.complete.assert_not_called()
    provider.count_tokens.assert_awaited()


@pytest.mark.asyncio
async def test_compressor_calls_the_summarizer_model_and_records_it(db_session):
    from sqlalchemy import select

    from vigilus.db.models import LlmUsage, UsageActorType

    seen: dict = {}

    class Summarizer:
        default_model = "claude-haiku-4-5"

        async def complete(self, messages, **kwargs):
            seen.update(kwargs)
            return LLMResponse(
                content="Kept the decision to patch nginx.",
                usage={"input_tokens": 12, "output_tokens": 4},
            )

    async def resolve_summary():
        return Summarizer(), "claude-haiku-4-5", None, "anthropic"

    msgs = [LLMMessage(role="user", content="x" * 400) for _ in range(20)]
    compressor = ContextCompressor(
        provider=AsyncMock(),
        model="claude-opus-4-8",
        max_tokens=500,
        trigger_threshold=0.5,
        resolve_summary=resolve_summary,
    )
    _result, summary = await compressor.compress_if_needed(msgs)

    assert summary is not None
    assert seen["model"] == "claude-haiku-4-5"
    assert seen.get("cache_conversation", False) is False
    rows = (await db_session.execute(select(LlmUsage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_type == UsageActorType.compression
    assert rows[0].model == "claude-haiku-4-5"
    assert rows[0].operator_id is None
    assert rows[0].input_tokens == 12
