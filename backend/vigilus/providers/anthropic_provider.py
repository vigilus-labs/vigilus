"""Anthropic provider implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic
from anthropic.types import MessageParam, ToolParam

from vigilus.providers.base import (
    AgentLLM,
    LLMMessage,
    LLMResponse,
    TextSink,
    ToolSpec,
    ToolUse,
    emit_text,
)
from vigilus.providers.catalog import ANTHROPIC_DEFAULT_MODEL

# 5-minute ephemeral cache. A breakpoint on the stable prefix, the tool list,
# and the latest message lets the next iteration read that prefix back.
_CACHE_CONTROL = {"type": "ephemeral"}


def _usage_from_message(usage: Any) -> dict[str, int]:
    """Map Anthropic usage onto the shared ledger shape.

    ``input_tokens`` excludes cache read and cache write. Those are recorded
    separately so cost can price them at the cache rates instead of full input.
    Zero cache counts are omitted so callers that compare the dict stay stable
    when caching is off.
    """
    counted = {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    if cache_read:
        counted["cache_read_tokens"] = cache_read
    if cache_write:
        counted["cache_write_tokens"] = cache_write
    return counted


def _mark_last_message(messages: list[dict[str, Any]]) -> None:
    """Put a cache breakpoint on the last message without mutating stored blocks."""
    if not messages:
        return
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        if content:
            last["content"] = [
                {"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL)}
            ]
        return
    if not isinstance(content, list) or not content:
        return
    blocks: list[Any] = []
    for block in content:
        blocks.append(dict(block) if isinstance(block, dict) else block)
    tail = blocks[-1]
    if isinstance(tail, dict):
        marked = dict(tail)
        marked["cache_control"] = dict(_CACHE_CONTROL)
        blocks[-1] = marked
    last["content"] = blocks


class AnthropicProvider(AgentLLM):
    """Adapter for Anthropic's Claude models."""

    def __init__(self, api_key: str, default_model: str = ANTHROPIC_DEFAULT_MODEL):
        self.api_key = api_key
        self.default_model = default_model
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    def _convert_messages(self, messages: list[LLMMessage]) -> list[MessageParam]:
        """Convert standard messages to Anthropic format."""
        converted = []
        for msg in messages:
            if msg.role == "user":
                converted.append({"role": "user", "content": msg.content})

            elif msg.role == "assistant":
                # Check if we have raw response blocks with tool_use
                if msg.raw and isinstance(msg.raw, dict) and "content" in msg.raw:
                    # Use the raw content blocks directly (includes text + tool_use blocks)
                    raw_content = msg.raw["content"]
                    if isinstance(raw_content, list):
                        # Anthropic raw content is a list of blocks
                        converted.append({"role": "assistant", "content": raw_content})
                    else:
                        converted.append({"role": "assistant", "content": msg.content or ""})
                elif hasattr(msg, "tool_calls") and msg.tool_calls:
                    # Build content blocks from tool_calls
                    blocks = []
                    if msg.content:
                        blocks.append({"type": "text", "text": str(msg.content)})
                    for tc in msg.tool_calls:
                        if isinstance(tc, dict):
                            tu_type = tc.get("type", "tool_use")
                            if tu_type == "tool_use":
                                blocks.append(
                                    {
                                        "type": "tool_use",
                                        "id": tc["id"],
                                        "name": tc["name"],
                                        "input": tc.get("input", tc.get("arguments", {})),
                                    }
                                )
                    if blocks:
                        converted.append({"role": "assistant", "content": blocks})
                    else:
                        converted.append({"role": "assistant", "content": msg.content or ""})
                else:
                    converted.append({"role": "assistant", "content": msg.content or ""})

            elif msg.role == "tool":
                if not msg.tool_use_id:
                    # No originating tool_use block (e.g. delegation result) —
                    # a tool_result with a bogus id would be rejected, so send
                    # it as a plain user message instead.
                    label = msg.name or "tool"
                    converted.append(
                        {
                            "role": "user",
                            "content": f"[Result from {label}]\n{msg.content}",
                        }
                    )
                    continue

                tool_result_content = msg.content
                if isinstance(tool_result_content, list):
                    # Already in block format
                    pass
                else:
                    tool_result_content = str(tool_result_content)

                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_use_id,
                                "content": tool_result_content,
                            }
                        ],
                    }
                )

        return converted

    def _convert_tools(self, tools: list[ToolSpec] | None) -> list[ToolParam]:
        """Convert standard tools to Anthropic format."""
        if not tools:
            return []

        converted = []
        for tool in tools:
            converted.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
            )
        return converted

    def _build_kwargs(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None,
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
        model: str | None = None,
        cached_system: str | None = None,
        cache_conversation: bool = False,
    ) -> dict[str, Any]:
        """Assemble the request payload shared by the streaming and plain paths."""
        had_messages = bool(messages)
        anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools)

        if not anthropic_messages:
            # Anthropic requires at least one message
            anthropic_messages = [{"role": "user", "content": "Hello"}]

        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anthropic_messages,
        }

        # Cache only when the caller opts in. A one-shot summary must not write
        # its unique prompt into the cache at the write premium.
        caching = bool(cached_system) or cache_conversation
        if cached_system:
            blocks: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": cached_system,
                    "cache_control": dict(_CACHE_CONTROL),
                }
            ]
            if system:
                blocks.append({"type": "text", "text": system})
            kwargs["system"] = blocks
        elif system:
            kwargs["system"] = system

        if anthropic_tools:
            if caching:
                last_tool = dict(anthropic_tools[-1])
                last_tool["cache_control"] = dict(_CACHE_CONTROL)
                anthropic_tools = [*anthropic_tools[:-1], last_tool]
            kwargs["tools"] = anthropic_tools

        if cache_conversation and had_messages:
            _mark_last_message(anthropic_messages)

        return kwargs

    def _to_response(self, response: Any) -> LLMResponse:
        """Map an Anthropic Message onto the provider-agnostic response."""
        content = ""
        tool_uses = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_uses.append(
                    ToolUse(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

        return LLMResponse(
            content=content,
            tool_uses=tool_uses,
            stop_reason=response.stop_reason,
            usage=_usage_from_message(response.usage),
            raw=response.model_dump(),
        )

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stream: bool = False,
        model: str | None = None,
        cached_system: str | None = None,
        cache_conversation: bool = False,
    ) -> LLMResponse | AsyncIterator[LLMResponse]:
        """Send completion to Anthropic."""
        kwargs = self._build_kwargs(
            messages,
            system=system,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            cached_system=cached_system,
            cache_conversation=cache_conversation,
        )

        if stream:
            return self._stream_complete(kwargs)

        response = await self.client.messages.create(**kwargs)
        return self._to_response(response)

    async def count_tokens(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
    ) -> int | None:
        """Anthropic's count_tokens endpoint. None on any failure."""
        kwargs = self._build_kwargs(
            messages,
            system=system,
            tools=None,
            temperature=0.0,
            max_tokens=1,
        )
        payload: dict[str, Any] = {
            "model": kwargs["model"],
            "messages": kwargs["messages"],
        }
        if system:
            payload["system"] = system
        try:
            counted = await self.client.messages.count_tokens(**payload)
            return int(counted.input_tokens)
        except Exception:  # noqa: BLE001 — fall back to the character heuristic
            return None

    async def complete_streaming(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        on_text: TextSink | None = None,
        model: str | None = None,
        cached_system: str | None = None,
        cache_conversation: bool = False,
    ) -> LLMResponse:
        """Stream text deltas, then return the assembled final message."""
        kwargs = self._build_kwargs(
            messages,
            system=system,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            cached_system=cached_system,
            cache_conversation=cache_conversation,
        )

        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                await emit_text(on_text, text)
            final = await stream.get_final_message()

        return self._to_response(final)

    async def _stream_complete(self, kwargs: dict) -> AsyncIterator[LLMResponse]:
        """Stream completion from Anthropic."""
        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield LLMResponse(content=text)

    async def test_connection(self) -> dict:
        """Verify the connection is valid."""
        try:
            await self.client.messages.create(
                model=self.default_model,
                max_tokens=1,
                messages=[{"role": "user", "content": "test"}],
            )
            return {"ok": True, "models": [self.default_model]}
        except Exception as e:
            return {"ok": False, "error": str(e)}
