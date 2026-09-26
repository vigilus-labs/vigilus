"""Google Generative AI provider implementation using the new google-genai SDK."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import types

from vigilus.providers.base import (
    AgentLLM,
    LLMMessage,
    LLMResponse,
    TextSink,
    ToolSpec,
    ToolUse,
    emit_text,
    join_system,
)


class GoogleProvider(AgentLLM):
    """Adapter for Google's Gemini models via the new genai SDK."""

    def __init__(self, api_key: str, default_model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.default_model = default_model
        self.client = genai.Client(api_key=api_key)

    def _convert_messages(self, messages: list[LLMMessage]) -> list[types.Content]:
        """Convert standard messages to Google genai Content format."""
        contents = []
        for msg in messages:
            if msg.role == "user":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=str(msg.content))],
                    )
                )
            elif msg.role == "assistant":
                parts = []
                if msg.content:
                    parts.append(types.Part.from_text(text=str(msg.content)))
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        if isinstance(tc, dict):
                            args = tc.get("input", tc.get("arguments", {}))
                            parts.append(
                                types.Part.from_function_call(
                                    name=tc.get("name", ""),
                                    args=args,
                                )
                            )
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
                else:
                    contents.append(
                        types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=str(msg.content or ""))],
                        )
                    )
            elif msg.role == "tool":
                if not msg.tool_use_id:
                    # No originating function_call (e.g. delegation result) —
                    # send as a plain user message instead of an orphan
                    # function_response.
                    label = msg.name or "tool"
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(text=f"[Result from {label}]\n{msg.content}")
                            ],
                        )
                    )
                    continue
                name = msg.name or "unknown"
                try:
                    result = (
                        json.loads(str(msg.content))
                        if isinstance(msg.content, str)
                        else msg.content
                    )
                except (json.JSONDecodeError, TypeError):
                    result = {"output": str(msg.content)}
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=name,
                                response=(
                                    result if isinstance(result, dict) else {"output": str(result)}
                                ),
                            )
                        ],
                    )
                )
        return contents

    def _convert_tools(self, tools: list[ToolSpec] | None) -> list[types.Tool] | None:
        if not tools:
            return None
        functions = []
        for tool in tools:
            functions.append(
                types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.input_schema if tool.input_schema else None,
                )
            )
        return [types.Tool(function_declarations=functions)]

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
        """Assemble the request payload shared by the streaming and plain paths.

        ``cache_conversation`` is Anthropic-only and ignored here. The cacheable
        prefix is still sent, joined onto the system instruction.
        """
        del cache_conversation
        system = join_system(cached_system, system)
        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system:
            config_kwargs["system_instruction"] = system

        return {
            "model": model or self.default_model,
            "contents": self._convert_messages(messages),
            "config": types.GenerateContentConfig(**config_kwargs),
            "tools": self._convert_tools(tools),
        }

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

        response = await self.client.aio.models.generate_content(
            **self._build_kwargs(
                messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                model=model,
                cached_system=cached_system,
                cache_conversation=cache_conversation,
            )
        )

        content = ""
        tool_uses = []

        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text:
                            content += part.text
                        elif part.function_call:
                            fc = part.function_call
                            args = {}
                            if fc.args:
                                args = dict(fc.args)
                            tool_uses.append(
                                ToolUse(
                                    id=f"call_{fc.name}_{len(tool_uses)}",
                                    name=fc.name,
                                    arguments=args,
                                )
                            )

        usage = {}
        if response.usage_metadata:
            usage = {
                "input_tokens": response.usage_metadata.prompt_token_count or 0,
                "output_tokens": response.usage_metadata.candidates_token_count or 0,
            }

        return LLMResponse(
            content=content,
            tool_uses=tool_uses,
            stop_reason="end_turn",
            usage=usage,
        )

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
        """Stream text chunks, then return the assembled final response.

        Tool-calling requests take the non-streaming path — see the note on
        :meth:`OpenAIProvider.complete_streaming`.
        """
        if tools:
            return await super().complete_streaming(
                messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                on_text=on_text,
                model=model,
                cached_system=cached_system,
                cache_conversation=cache_conversation,
            )

        parts: list[str] = []
        usage: dict[str, int] = {}
        emitted = False

        try:
            stream = await self.client.aio.models.generate_content_stream(
                **self._build_kwargs(
                    messages,
                    system=system,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    model=model,
                    cached_system=cached_system,
                    cache_conversation=cache_conversation,
                )
            )
            async for chunk in stream:
                if chunk.usage_metadata:
                    usage = {
                        "input_tokens": chunk.usage_metadata.prompt_token_count or 0,
                        "output_tokens": chunk.usage_metadata.candidates_token_count or 0,
                    }
                text = getattr(chunk, "text", None)
                if text:
                    parts.append(text)
                    emitted = True
                    await emit_text(on_text, text)
        except Exception:
            if emitted:
                # Already partly on screen — re-running would duplicate it.
                raise
            return await super().complete_streaming(
                messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                on_text=on_text,
                model=model,
                cached_system=cached_system,
                cache_conversation=cache_conversation,
            )

        return LLMResponse(
            content="".join(parts),
            tool_uses=[],
            stop_reason="end_turn",
            usage=usage,
        )

    async def _stream_complete(self, kwargs: dict) -> AsyncIterator[LLMResponse]:
        # Streaming not yet fully supported in genai async client
        # Fall back to single response
        response = await self.client.aio.models.generate_content(**kwargs)
        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text:
                            yield LLMResponse(content=part.text)

    async def test_connection(self) -> dict:
        try:
            models = self.client.models.list()
            model_ids = [m.name for m in models]
            return {"ok": True, "models": model_ids}
        except Exception as e:
            return {"ok": False, "error": str(e)}
