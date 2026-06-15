"""Anthropic provider implementation."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import anthropic
from anthropic.types import MessageParam, ToolParam

from vigilus.providers.base import (
    AgentLLM,
    LLMMessage,
    LLMResponse,
    ToolSpec,
    ToolUse,
)


class AnthropicProvider(AgentLLM):
    """Adapter for Anthropic's Claude models."""

    def __init__(self, api_key: str, default_model: str = "claude-3-5-sonnet-20241022"):
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
                elif hasattr(msg, 'tool_calls') and msg.tool_calls:
                    # Build content blocks from tool_calls
                    blocks = []
                    if msg.content:
                        blocks.append({"type": "text", "text": str(msg.content)})
                    for tc in msg.tool_calls:
                        if isinstance(tc, dict):
                            tu_type = tc.get("type", "tool_use")
                            if tu_type == "tool_use":
                                blocks.append({
                                    "type": "tool_use",
                                    "id": tc["id"],
                                    "name": tc["name"],
                                    "input": tc.get("input", tc.get("arguments", {})),
                                })
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
                    converted.append({
                        "role": "user",
                        "content": f"[Result from {label}]\n{msg.content}",
                    })
                    continue

                tool_result_content = msg.content
                if isinstance(tool_result_content, list):
                    # Already in block format
                    pass
                else:
                    tool_result_content = str(tool_result_content)

                converted.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_use_id,
                            "content": tool_result_content,
                        }
                    ],
                })

        return converted

    def _convert_tools(self, tools: list[ToolSpec] | None) -> list[ToolParam]:
        """Convert standard tools to Anthropic format."""
        if not tools:
            return []

        converted = []
        for tool in tools:
            converted.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            })
        return converted

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> LLMResponse | AsyncIterator[LLMResponse]:
        """Send completion to Anthropic."""
        anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools)

        if not anthropic_messages:
            # Anthropic requires at least one message
            anthropic_messages = [{"role": "user", "content": "Hello"}]

        kwargs: dict[str, Any] = {
            "model": self.default_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anthropic_messages,
        }

        if system:
            kwargs["system"] = system

        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        if stream:
            return self._stream_complete(kwargs)

        response = await self.client.messages.create(**kwargs)

        content = ""
        tool_uses = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_uses.append(ToolUse(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))

        return LLMResponse(
            content=content,
            tool_uses=tool_uses,
            stop_reason=response.stop_reason,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            raw=response.model_dump(),
        )

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
