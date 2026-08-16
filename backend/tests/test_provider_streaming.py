"""Provider streaming: `complete_streaming` must deliver text as it arrives
and still return the same fully-formed response `complete` would have.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vigilus.providers.anthropic_provider import AnthropicProvider
from vigilus.providers.base import AgentLLM, LLMMessage, LLMResponse, ToolSpec
from vigilus.providers.openai_provider import OpenAIProvider

MESSAGES = [LLMMessage(role="user", content="check my servers")]


# ── Fakes ────────────────────────────────────────────────────────────────


class _AsyncStream:
    """Minimal stand-in for an SDK's async streaming response."""

    def __init__(self, items, *, fail_after: int | None = None):
        self._items = items
        self._fail_after = fail_after

    async def __aiter__(self):
        for i, item in enumerate(self._items):
            if self._fail_after is not None and i == self._fail_after:
                raise RuntimeError("connection reset mid-stream")
            yield item


def _chunk(text=None, finish=None, usage=None):
    """An OpenAI streaming chunk. `choices=[]` mimics the usage-only trailer."""
    choices = []
    if text is not None or finish is not None:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)]
    return SimpleNamespace(choices=choices, usage=usage)


# ── Base-class fallback ──────────────────────────────────────────────────


class _NonStreamingProvider(AgentLLM):
    """A provider that never overrode complete_streaming."""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        return LLMResponse(content="all done", usage={"input_tokens": 5, "output_tokens": 2})

    async def test_connection(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_base_fallback_delivers_text_in_one_piece():
    provider = _NonStreamingProvider()
    seen: list[str] = []

    response = await provider.complete_streaming(MESSAGES, on_text=seen.append)

    assert seen == ["all done"]
    assert response.content == "all done"
    assert response.usage == {"input_tokens": 5, "output_tokens": 2}
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_a_broken_sink_never_fails_the_completion():
    """The reply matters more than the display that was showing it."""
    provider = _NonStreamingProvider()

    def _explode(_text):
        raise RuntimeError("the browser went away")

    response = await provider.complete_streaming(MESSAGES, on_text=_explode)

    assert response.content == "all done"


# ── OpenAI ───────────────────────────────────────────────────────────────


def _openai_provider(chunks, **stream_kwargs):
    provider = OpenAIProvider(api_key="sk-test", default_model="gpt-4o")
    provider.client = MagicMock()
    provider.client.chat.completions.create = AsyncMock(
        return_value=_AsyncStream(chunks, **stream_kwargs)
    )
    return provider


@pytest.mark.asyncio
async def test_openai_streams_deltas_and_totals_usage():
    provider = _openai_provider(
        [
            _chunk("I'll have "),
            _chunk("the Systems Operator "),
            _chunk("check.", finish="stop"),
            _chunk(usage=SimpleNamespace(prompt_tokens=120, completion_tokens=17)),
        ]
    )
    seen: list[str] = []

    response = await provider.complete_streaming(MESSAGES, on_text=seen.append)

    assert seen == ["I'll have ", "the Systems Operator ", "check."]
    assert response.content == "I'll have the Systems Operator check."
    assert response.stop_reason == "stop"
    # Streamed turns must still land in token/cost accounting.
    assert response.usage == {"input_tokens": 120, "output_tokens": 17}

    kwargs = provider.client.chat.completions.create.await_args.kwargs
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_openai_tool_calls_take_the_non_streaming_path():
    """Tool arguments hit real infrastructure — never reassemble them from deltas."""
    provider = _openai_provider([_chunk("ignored")])
    plain = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="done",
                    tool_calls=None,
                    model_dump=lambda exclude_none: {"role": "assistant", "content": "done"},
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        error=None,
    )
    provider.client.chat.completions.create = AsyncMock(return_value=plain)

    response = await provider.complete_streaming(
        MESSAGES,
        tools=[ToolSpec(name="shell_exec", description="run", input_schema={})],
        on_text=lambda _t: None,
    )

    assert response.content == "done"
    assert provider.client.chat.completions.create.await_args.kwargs.get("stream") is None


@pytest.mark.asyncio
async def test_openai_falls_back_when_the_stream_fails_before_any_text():
    """Gateways that reject stream_options must still answer the turn."""
    provider = OpenAIProvider(api_key="sk-test", default_model="gpt-4o")
    provider.client = MagicMock()
    plain = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="recovered",
                    tool_calls=None,
                    model_dump=lambda exclude_none: {"role": "assistant"},
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
        error=None,
    )
    calls = {"n": 0}

    async def _create(**kwargs):
        calls["n"] += 1
        if kwargs.get("stream"):
            raise RuntimeError("stream_options not supported")
        return plain

    provider.client.chat.completions.create = _create
    seen: list[str] = []

    response = await provider.complete_streaming(MESSAGES, on_text=seen.append)

    assert response.content == "recovered"
    assert seen == ["recovered"]
    assert calls["n"] == 2  # streamed attempt, then the plain one


@pytest.mark.asyncio
async def test_openai_raises_when_the_stream_fails_after_text_was_shown():
    """Retrying here would print the first half of the answer twice."""
    provider = _openai_provider([_chunk("I'll have "), _chunk("more")], fail_after=1)
    seen: list[str] = []

    with pytest.raises(RuntimeError):
        await provider.complete_streaming(MESSAGES, on_text=seen.append)

    assert seen == ["I'll have "]


# ── Anthropic ────────────────────────────────────────────────────────────


class _AnthropicStreamCtx:
    def __init__(self, texts, final):
        self.text_stream = _AsyncStream(texts)
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self._final


@pytest.mark.asyncio
async def test_anthropic_streams_then_returns_the_final_message():
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="I'll have the Systems Operator check.")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=200, output_tokens=12),
        model_dump=lambda: {"role": "assistant"},
    )
    provider = AnthropicProvider(api_key="sk-test", default_model="claude-sonnet-5")
    provider.client = MagicMock()
    provider.client.messages.stream = MagicMock(
        return_value=_AnthropicStreamCtx(["I'll have ", "the Systems Operator ", "check."], final)
    )
    seen: list[str] = []

    response = await provider.complete_streaming(MESSAGES, on_text=seen.append)

    assert seen == ["I'll have ", "the Systems Operator ", "check."]
    assert response.content == "I'll have the Systems Operator check."
    assert response.usage == {"input_tokens": 200, "output_tokens": 12}
    assert response.stop_reason == "end_turn"
