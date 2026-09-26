"""OpenAI provider implementation."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import openai

from vigilus.providers.base import (
    AgentLLM,
    LLMMessage,
    LLMResponse,
    ProviderError,
    TextSink,
    ToolSpec,
    ToolUse,
    emit_text,
    join_system,
    retry_transient,
)


class OpenAIProvider(AgentLLM):
    """Adapter for OpenAI models."""

    def __init__(self, api_key: str, default_model: str = "gpt-4o"):
        self.api_key = api_key
        self.default_model = default_model
        self.client = openai.AsyncOpenAI(api_key=api_key)

    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        converted = []
        for msg in messages:
            if msg.role == "user":
                converted.append({"role": "user", "content": str(msg.content)})

            elif msg.role == "assistant":
                # Check if we have raw response (OpenAI format)
                if msg.raw and isinstance(msg.raw, dict):
                    raw = dict(msg.raw)
                    # Ensure role is set
                    if "role" not in raw:
                        raw["role"] = "assistant"
                    # If there's content but it's empty and we have tool_calls, still include content
                    converted.append(raw)
                elif hasattr(msg, "tool_calls") and msg.tool_calls:
                    # Build tool_calls in OpenAI format from stored data
                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": str(msg.content) if msg.content else None,
                        "tool_calls": [],
                    }
                    for tc in msg.tool_calls:
                        if isinstance(tc, dict):
                            func_name = tc.get("name", tc.get("function", {}).get("name", ""))
                            func_args = tc.get(
                                "input",
                                tc.get("arguments", tc.get("function", {}).get("arguments", {})),
                            )
                            tc_id = tc.get("id", f"call_{hash(func_name)}")

                            if isinstance(func_args, dict):
                                func_args = json.dumps(func_args)

                            assistant_msg["tool_calls"].append(
                                {
                                    "id": tc_id,
                                    "type": "function",
                                    "function": {
                                        "name": func_name,
                                        "arguments": func_args,
                                    },
                                }
                            )
                    converted.append(assistant_msg)
                else:
                    converted.append(
                        {
                            "role": "assistant",
                            "content": str(msg.content) if msg.content else "",
                        }
                    )

            elif msg.role == "tool":
                if msg.tool_use_id:
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": msg.tool_use_id,
                            "content": str(msg.content),
                        }
                    )
                else:
                    # No originating tool_call (e.g. delegation result) —
                    # strict providers reject orphan tool messages, so send
                    # it as a plain user message instead.
                    label = msg.name or "tool"
                    converted.append(
                        {
                            "role": "user",
                            "content": f"[Result from {label}]\n{msg.content}",
                        }
                    )

        return converted

    def _convert_tools(self, tools: list[ToolSpec] | None) -> list[dict]:
        if not tools:
            return []

        converted = []
        for tool in tools:
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
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
        """Assemble the request payload shared by the streaming and plain paths.

        ``cache_conversation`` is Anthropic-only and ignored here. The cacheable
        prefix is still sent, joined onto the system string.
        """
        del cache_conversation
        system = join_system(cached_system, system)
        openai_messages = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(self._convert_messages(messages))

        openai_tools = self._convert_tools(tools)

        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if openai_tools:
            kwargs["tools"] = openai_tools

        return kwargs

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

        # Some OpenAI-compatible gateways (e.g. OpenRouter free tier) answer an
        # overloaded/timeout upstream with HTTP 200 + an in-body error and
        # choices=None instead of raising. Wrap BOTH the call and the validity
        # check in retry_transient so transient in-body errors (504/503/429…)
        # are retried, and turn the rest into a typed ProviderError rather
        # than a TypeError when we subscript choices below.
        async def _do_request():
            response = await self.client.chat.completions.create(**kwargs)
            body_err = getattr(response, "error", None)
            if body_err or not response.choices:
                code = body_err.get("code") if isinstance(body_err, dict) else None
                message = (
                    body_err.get("message") if isinstance(body_err, dict) else None
                ) or "upstream returned no choices"
                raise ProviderError(
                    f"Upstream LLM error (code={code}): {message}",
                    status_code=code if isinstance(code, int) else None,
                )
            return response

        response = await retry_transient(_do_request)
        choice = response.choices[0]
        msg = choice.message

        content = msg.content or ""
        tool_uses = []

        if msg.tool_calls:
            for call in msg.tool_calls:
                if call.type == "function":
                    try:
                        arguments = json.loads(call.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    tool_uses.append(
                        ToolUse(
                            id=call.id,
                            name=call.function.name,
                            arguments=arguments,
                        )
                    )

        return LLMResponse(
            content=content,
            tool_uses=tool_uses,
            stop_reason=choice.finish_reason,
            usage={
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
            },
            raw=msg.model_dump(exclude_none=True),
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
        """Stream text deltas, then return the assembled final response.

        Tool-calling requests take the non-streaming path: reassembling
        fragmented ``tool_calls`` deltas would risk mangling arguments that go
        on to hit real infrastructure, and nothing displays that text live
        anyway. Only the orchestrator (which has no tools) streams today.
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
        kwargs["stream"] = True
        # Streamed responses omit usage unless it is asked for; without it the
        # turn would be invisible to token/cost accounting.
        kwargs["stream_options"] = {"include_usage": True}

        parts: list[str] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        finish_reason: str | None = None
        emitted = False

        try:
            stream = await self.client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = {
                        "input_tokens": chunk.usage.prompt_tokens or 0,
                        "output_tokens": chunk.usage.completion_tokens or 0,
                    }
                # The usage-only trailer (and some gateways' keepalives) carry
                # no choices at all.
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = getattr(choice.delta, "content", None)
                if delta:
                    parts.append(delta)
                    emitted = True
                    await emit_text(on_text, delta)
        except Exception:
            if emitted:
                # Text is already on the user's screen — retrying would
                # duplicate it, so let the caller handle the failure.
                raise
            # Nothing shown yet: fall back to the non-streaming path, which
            # retries transient errors and copes with gateways that reject
            # `stream_options`.
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

        content = "".join(parts)
        return LLMResponse(
            content=content,
            tool_uses=[],
            stop_reason=finish_reason,
            usage=usage,
            raw={"role": "assistant", "content": content},
        )

    async def _stream_complete(self, kwargs: dict) -> AsyncIterator[LLMResponse]:
        kwargs["stream"] = True
        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.delta.content:
                yield LLMResponse(content=choice.delta.content)

    async def test_connection(self) -> dict:
        try:
            models = await self.client.models.list()
            return {"ok": True, "models": [m.id for m in models.data]}
        except Exception as e:
            return {"ok": False, "error": str(e)}
