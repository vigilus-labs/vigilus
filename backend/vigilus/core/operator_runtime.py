"""Runtime loop for executing an Operator."""

from __future__ import annotations

import json
from typing import Any

import structlog

from vigilus.core.audit import redact_args
from vigilus.core.tasks import TaskCancelled, await_cancelled, get_task_registry
from vigilus.db.models import Operator
from vigilus.providers.base import LLMMessage, ToolSpec
from vigilus.providers.registry import build_provider
from vigilus.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)


def _call_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    """Canonical signature of a tool call for loop detection.

    Arguments are serialized with sorted keys so the same call made by an LLM
    with different key ordering is still recognized as a repeat. Computed on
    the raw arguments (before redaction) — redaction would make two different
    secrets look identical and hide a real loop.
    """
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return f"{tool_name}:{canonical}"


_ITERATION_LIMIT_PROMPT = (
    "You have reached the iteration limit and cannot call any more tools. "
    "Summarize what you have found so far and what remains unfinished. "
    "Do not request further tool calls."
)

_ITERATION_LIMIT_FALLBACK = (
    "Iteration limit reached before a final report. "
    "The tool results above are the findings so far; the task may be unfinished."
)


def cap_tool_output(text: str, max_chars: int) -> str:
    """Keep the head and tail of an oversized tool result.

    ``max_chars`` of 0 or less disables the cap. The marker reports how many
    characters were omitted. The returned string is never longer than
    ``max_chars`` when the cap is enabled and the input exceeds it.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    def marker_for(omitted: int) -> str:
        return f"\n[... {omitted} bytes truncated ...]\n"

    omitted = len(text) - max_chars
    for _ in range(8):
        marker = marker_for(max(omitted, 0))
        if len(marker) >= max_chars:
            break
        budget = max_chars - len(marker)
        head = budget // 2
        tail = budget - head
        new_omitted = len(text) - head - tail
        if new_omitted == omitted:
            return text[:head] + marker + text[-tail:]
        omitted = new_omitted

    note = f"\n[... {len(text)} bytes truncated ...]"
    if len(note) >= max_chars:
        return text[:max_chars]
    keep = max_chars - len(note)
    omitted = len(text) - keep
    note = f"\n[... {omitted} bytes truncated ...]"
    if len(note) >= max_chars:
        return text[:max_chars]
    keep = max_chars - len(note)
    return text[:keep] + note


def _args_preview(redacted: dict[str, Any] | None, limit: int = 120) -> str:
    """Compact one-line rendering of redacted tool arguments for live feeds."""
    if not redacted:
        return ""
    text = json.dumps(redacted, default=str, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


class OperatorRuntime:
    """Executes an operator's logic against its provider, resolving tools dynamically."""

    def __init__(self, operator: Operator, fallback_provider=None):
        self.operator = operator
        # Prefer the operator's own provider; otherwise fall back to the
        # platform default (passed in by the caller). This lets freshly
        # seeded operators work as soon as any provider is configured,
        # without forcing the user to assign one to every operator.
        provider_row = operator.provider or fallback_provider
        if not provider_row:
            raise ValueError(
                f"Operator '{operator.name}' has no provider configured and no "
                "default provider is available. Add a provider in Settings."
            )
        self.provider = build_provider(provider_row)
        if operator.model and hasattr(self.provider, "default_model"):
            self.provider.default_model = operator.model
        self._provider_id = provider_row.id
        self._provider_type = (
            provider_row.type.value
            if hasattr(provider_row.type, "value")
            else str(provider_row.type)
        )
        self._model = operator.model or provider_row.default_model
        self.tool_registry = ToolRegistry()

    async def _get_tools(self) -> list[ToolSpec]:
        """Convert assigned DB tools to ToolSpecs for the provider."""
        tools = []
        for ot in self.operator.operator_tools:
            tool = ot.tool
            tools.append(
                ToolSpec(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.input_schema or {},
                )
            )
        return tools

    async def _build_system_prompt(self, tools: list[ToolSpec]) -> str | None:
        """Compose the operator's system prompt: base + soul + recalled memories.

        Memories from the "global" scope (shared environment knowledge) and the
        operator's own scope are injected so the operator retains what it has
        learned about the environment across sessions.
        """
        from vigilus.core.memory import get_memories, render_memory_block
        from vigilus.db.base import get_session_factory

        parts: list[str] = []
        if self.operator.system_prompt:
            parts.append(self.operator.system_prompt)
        if self.operator.soul:
            parts.append(f"## Your soul\n\n{self.operator.soul.strip()}")

        try:
            factory = get_session_factory()
            async with factory() as db:
                memories = await get_memories(db, ["global", self.operator.id])
            block = render_memory_block(memories)
            if block:
                parts.append(block)
        except Exception as e:
            logger.warning(
                "operator.memory_recall_failed", operator=self.operator.name, error=str(e)
            )

        if any(t.name == "memory_save" for t in tools):
            parts.append(
                "## Learning the environment\n\n"
                "When you discover a durable fact worth keeping — what a server's role "
                "is, what services it runs, an environment quirk, a user preference — "
                "save it with the memory_save tool (scope 'global' for environment "
                "knowledge, 'self' for private notes). Use memory_forget to remove "
                "facts that turn out to be wrong. Don't save transient state like "
                "current CPU usage or one-off command output."
            )

        return "\n\n".join(parts) if parts else None

    async def run(
        self,
        messages: list[LLMMessage],
        session_id: str | None = None,
        jit_token: str | None = None,
        max_iterations: int | None = None,
        bridge: Any | None = None,  # StreamBridge from api.sse
        cancel_event: Any | None = None,  # asyncio.Event — stop when set
        unattended: bool = False,  # scheduled run — use longer JIT wait
    ) -> tuple[list[LLMMessage], list[dict[str, Any]]]:
        """Run the operator loop until a final text response is generated.

        Args:
            messages: Conversation history so far. Will be mutated with new messages.
            session_id: The chat session ID (for audit logs).
            jit_token: Optional JIT token for elevated privileges.
            max_iterations: Safety limit to prevent infinite tool loops.
                None uses this operator's limit, or the global default.

        Returns:
            A tuple of:
              - The mutated messages list including all new messages.
              - A list of tool call history dicts (for logging).
        """
        tools = await self._get_tools()
        system_prompt = await self._build_system_prompt(tools)
        tool_history: list[dict[str, Any]] = []

        from vigilus.config import get_settings

        settings = get_settings()
        if max_iterations is None:
            operator_limit = getattr(self.operator, "max_iterations", None)
            max_iterations = operator_limit or settings.operator_max_iterations
        # 0 disables loop detection entirely.
        loop_threshold = settings.loop_detection_threshold
        last_signature: str | None = None
        repeat_count = 0
        loop_detected = False

        for iteration in range(max_iterations):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("operator.cancelled", operator=self.operator.name)
                break

            # Budget hard stop: the platform-wide cap and this operator's own
            # cap (if set) both apply. Stopping here means no provider call is
            # made; the notice becomes the delegation report so the
            # orchestrator can wrap up gracefully.
            from vigilus.core.budget import turn_budget_stop
            from vigilus.db.base import get_session_factory

            budget_factory = get_session_factory()
            async with budget_factory() as budget_db:
                budget_stop = await turn_budget_stop(budget_db, operator=self.operator)
            if budget_stop:
                logger.warning("operator.budget_stop", operator=self.operator.name)
                messages.append(LLMMessage(role="assistant", content=budget_stop))
                tool_history.append({"budget_stop": True})
                if bridge:
                    bridge.publish(
                        "budget_stop",
                        {"operator": self.operator.name},
                    )
                break

            # Live progress for the Tasks page / operator drawer.
            if session_id:
                get_task_registry().update(
                    session_id,
                    step=(f"{self.operator.name}: iteration " f"{iteration + 1}/{max_iterations}"),
                )

            logger.info(
                "operator.run_iteration",
                operator=self.operator.name,
                iteration=iteration + 1,
                tool_count=len(tools),
            )

            try:
                response = await await_cancelled(
                    self.provider.complete(
                        messages=messages,
                        system=system_prompt,
                        tools=tools,
                        temperature=0.0,
                    ),
                    cancel_event,
                    timeout=settings.llm_request_timeout_seconds,
                )
            except TaskCancelled:
                logger.info("operator.cancelled_while_waiting", operator=self.operator.name)
                raise

            from vigilus.core.llm_usage import record_llm_usage
            from vigilus.db.models import UsageActorType

            await record_llm_usage(
                usage=response.usage or {},
                actor_type=UsageActorType.operator,
                operator_id=self.operator.id,
                session_id=session_id,
                provider_id=self._provider_id,
                provider_type=self._provider_type,
                model=getattr(self.provider, "default_model", None) or self._model,
            )

            # Build assistant message
            assistant_content = response.content or ""

            if response.tool_uses:
                # Intermediate assistant message – has tool calls
                # Build tool_calls list for LLMMessage
                tool_call_dicts = []
                for tu in response.tool_uses:
                    tool_call_dicts.append(
                        {
                            "type": "tool_use",
                            "id": tu.id,
                            "name": tu.name,
                            "input": tu.arguments,
                        }
                    )

                assistant_msg = LLMMessage(
                    role="assistant",
                    content=assistant_content,
                    tool_calls=tool_call_dicts,
                )
                # Store raw response for Anthropic's format (needed for tool_result blocks)
                if hasattr(response, "raw") and response.raw:
                    assistant_msg.raw = response.raw
                # For OpenAI compatibility, also store raw
                elif hasattr(response, "raw") and response.raw:
                    assistant_msg.raw = response.raw

                messages.append(assistant_msg)

                # Execute each tool call
                for idx, tool_use in enumerate(response.tool_uses):
                    if cancel_event is not None and cancel_event.is_set():
                        logger.info("operator.cancelled_midtools", operator=self.operator.name)
                        # Synthesize a tool result so message history stays valid
                        # (every tool_use needs a matching tool result block).
                        messages.append(
                            LLMMessage(
                                role="tool",
                                name=tool_use.name,
                                tool_use_id=tool_use.id,
                                content="Cancelled by user before execution.",
                            )
                        )
                        continue

                    # ── Loop detection ─────────────────────────────
                    # Signature uses the raw (unredacted) arguments: redaction
                    # would collapse different secrets into one value and hide
                    # a real repeat.
                    signature = _call_signature(tool_use.name, dict(tool_use.arguments or {}))
                    if signature == last_signature:
                        repeat_count += 1
                    else:
                        last_signature = signature
                        repeat_count = 1

                    if loop_threshold and repeat_count >= loop_threshold:
                        logger.warning(
                            "operator.loop_detected",
                            operator=self.operator.name,
                            tool=tool_use.name,
                            count=repeat_count,
                        )
                        notice = (
                            f"I stopped because I was about to call `{tool_use.name}` "
                            f"{repeat_count} times in a row with identical arguments — "
                            f"this looks like a loop, so I aborted instead of repeating "
                            f"it. I could not make further progress on the task this "
                            f"way. A different approach is needed: different arguments, "
                            f"a different tool, or an honest report of what is blocking "
                            f"progress."
                        )
                        if bridge:
                            bridge.publish(
                                "loop_detected",
                                {
                                    "operator": self.operator.name,
                                    "tool": tool_use.name,
                                    "count": repeat_count,
                                    "args_preview": _args_preview(
                                        redact_args(dict(tool_use.arguments or {}))
                                    ),
                                },
                            )
                        # Every remaining tool_use in this batch (including the
                        # blocked one) gets a synthesized result so the message
                        # history stays valid for strict providers.
                        for remaining in response.tool_uses[idx:]:
                            messages.append(
                                LLMMessage(
                                    role="tool",
                                    name=remaining.name,
                                    tool_use_id=remaining.id,
                                    content=(
                                        "Blocked: run aborted by loop detection "
                                        "(repeated identical tool call)."
                                    ),
                                )
                            )
                        messages.append(LLMMessage(role="assistant", content=notice))
                        tool_history.append(
                            {
                                "loop_detected": True,
                                "tool": tool_use.name,
                                "count": repeat_count,
                            }
                        )
                        loop_detected = True
                        break

                    logger.info(
                        "operator.tool_call",
                        name=tool_use.name,
                        args_keys=list(tool_use.arguments.keys()) if tool_use.arguments else [],
                    )

                    if session_id:
                        get_task_registry().update(
                            session_id,
                            step=(
                                f"{self.operator.name}: calling {tool_use.name} "
                                f"(iteration {iteration + 1}/{max_iterations})"
                            ),
                        )

                    if bridge:
                        # Redact BEFORE publishing: this fires before
                        # ToolRegistry.execute() pops jit_token, so the raw
                        # arguments still contain any credential the LLM
                        # passed. Nothing unredacted may reach the SSE stream
                        # or the in-memory activity buffer.
                        safe_args = redact_args(dict(tool_use.arguments or {}))
                        bridge.publish(
                            "tool_call",
                            {
                                "tool": tool_use.name,
                                "operator": self.operator.name,
                                "args": safe_args,
                                "args_preview": _args_preview(safe_args),
                                "iteration": iteration + 1,
                                "max_iterations": max_iterations,
                            },
                        )

                    result = await self.tool_registry.execute(
                        tool_id_or_name=tool_use.name,
                        arguments=tool_use.arguments,
                        operator=self.operator,
                        session_id=session_id,
                        jit_token=jit_token,
                        unattended=unattended,
                        cancel_event=cancel_event,
                    )

                    raw_output = result.output if result.success else f"Error: {result.error}"
                    raw_output = raw_output or ""
                    tool_output = cap_tool_output(raw_output, settings.tool_output_max_chars)
                    tool_msg = LLMMessage(
                        role="tool",
                        name=tool_use.name,
                        tool_use_id=tool_use.id,
                        content=tool_output,
                    )
                    messages.append(tool_msg)

                    if bridge:
                        bridge.publish(
                            "tool_result",
                            {
                                "tool": tool_use.name,
                                "operator": self.operator.name,
                                "success": result.success,
                                "preview": tool_output[:300],
                            },
                        )

                    tool_history.append(
                        {
                            "tool": tool_use.name,
                            "arguments": tool_use.arguments,
                            "success": result.success,
                            "output_preview": (result.output or result.error or "")[:200],
                        }
                    )
            else:
                # Final assistant message – no tool calls
                assistant_msg = LLMMessage(
                    role="assistant",
                    content=assistant_content,
                )
                messages.append(assistant_msg)
                break

            if loop_detected:
                break
        else:
            await self._summarize_after_iteration_limit(
                messages,
                system_prompt,
                tool_history,
                max_iterations=max_iterations,
                session_id=session_id,
                cancel_event=cancel_event,
                settings=settings,
            )

        return messages, tool_history

    async def _summarize_after_iteration_limit(
        self,
        messages: list[LLMMessage],
        system_prompt: str | None,
        tool_history: list[dict[str, Any]],
        *,
        max_iterations: int,
        session_id: str | None,
        cancel_event: Any | None,
        settings: Any,
    ) -> None:
        """One last call with tools disabled so a capped run still reports."""
        logger.warning(
            "operator.iteration_limit",
            operator=self.operator.name,
            max_iterations=max_iterations,
        )
        messages.append(LLMMessage(role="user", content=_ITERATION_LIMIT_PROMPT))
        response = await await_cancelled(
            self.provider.complete(
                messages=messages,
                system=system_prompt,
                tools=None,
                temperature=0.0,
            ),
            cancel_event,
            timeout=settings.llm_request_timeout_seconds,
        )

        from vigilus.core.llm_usage import record_llm_usage
        from vigilus.db.models import UsageActorType

        await record_llm_usage(
            usage=response.usage or {},
            actor_type=UsageActorType.operator,
            operator_id=self.operator.id,
            session_id=session_id,
            provider_id=self._provider_id,
            provider_type=self._provider_type,
            model=getattr(self.provider, "default_model", None) or self._model,
        )

        summary = (response.content or "").strip() or _ITERATION_LIMIT_FALLBACK
        messages.append(LLMMessage(role="assistant", content=summary))
        tool_history.append(
            {
                "iteration_limit_reached": True,
                "max_iterations": max_iterations,
            }
        )
