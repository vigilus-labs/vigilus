"""Runtime loop for executing an Operator."""

from __future__ import annotations

import structlog
from typing import Any

from vigilus.db.models import Operator
from vigilus.providers.base import LLMMessage, ToolSpec, ToolUse
from vigilus.providers.registry import build_provider
from vigilus.tools.registry import ToolRegistry
from vigilus.core.tasks import TaskCancelled, await_cancelled

logger = structlog.get_logger(__name__)


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
        self.tool_registry = ToolRegistry()

    async def _get_tools(self) -> list[ToolSpec]:
        """Convert assigned DB tools to ToolSpecs for the provider."""
        tools = []
        for ot in self.operator.operator_tools:
            tool = ot.tool
            tools.append(ToolSpec(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.input_schema or {},
            ))
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
            logger.warning("operator.memory_recall_failed",
                           operator=self.operator.name, error=str(e))

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
        max_iterations: int = 15,
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

        Returns:
            A tuple of:
              - The mutated messages list including all new messages.
              - A list of tool call history dicts (for logging).
        """
        tools = await self._get_tools()
        system_prompt = await self._build_system_prompt(tools)
        tool_history: list[dict[str, Any]] = []

        for iteration in range(max_iterations):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("operator.cancelled", operator=self.operator.name)
                break

            logger.info(
                "operator.run_iteration",
                operator=self.operator.name,
                iteration=iteration + 1,
                tool_count=len(tools),
            )

            try:
                from vigilus.config import get_settings

                response = await await_cancelled(
                    self.provider.complete(
                        messages=messages,
                        system=system_prompt,
                        tools=tools,
                        temperature=0.0,
                    ),
                    cancel_event,
                    timeout=get_settings().llm_request_timeout_seconds,
                )
            except TaskCancelled:
                logger.info("operator.cancelled_while_waiting", operator=self.operator.name)
                raise

            # Build assistant message
            assistant_content = response.content or ""

            if response.tool_uses:
                # Intermediate assistant message – has tool calls
                # Build tool_calls list for LLMMessage
                tool_call_dicts = []
                for tu in response.tool_uses:
                    tool_call_dicts.append({
                        "type": "tool_use",
                        "id": tu.id,
                        "name": tu.name,
                        "input": tu.arguments,
                    })

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
                for tool_use in response.tool_uses:
                    if cancel_event is not None and cancel_event.is_set():
                        logger.info("operator.cancelled_midtools", operator=self.operator.name)
                        # Synthesize a tool result so message history stays valid
                        # (every tool_use needs a matching tool result block).
                        messages.append(LLMMessage(
                            role="tool",
                            name=tool_use.name,
                            tool_use_id=tool_use.id,
                            content="Cancelled by user before execution.",
                        ))
                        continue

                    logger.info(
                        "operator.tool_call",
                        name=tool_use.name,
                        args_keys=list(tool_use.arguments.keys()),
                    )

                    if bridge:
                        bridge.publish("tool_call", {
                            "tool": tool_use.name,
                            "operator": self.operator.name,
                        })

                    result = await self.tool_registry.execute(
                        tool_id_or_name=tool_use.name,
                        arguments=tool_use.arguments,
                        operator=self.operator,
                        session_id=session_id,
                        jit_token=jit_token,
                        unattended=unattended,
                        cancel_event=cancel_event,
                    )

                    tool_output = result.output if result.success else f"Error: {result.error}"
                    tool_msg = LLMMessage(
                        role="tool",
                        name=tool_use.name,
                        tool_use_id=tool_use.id,
                        content=tool_output,
                    )
                    messages.append(tool_msg)

                    if bridge:
                        bridge.publish("tool_result", {
                            "tool": tool_use.name,
                            "operator": self.operator.name,
                            "success": result.success,
                            "preview": tool_output[:300],
                        })

                    tool_history.append({
                        "tool": tool_use.name,
                        "arguments": tool_use.arguments,
                        "success": result.success,
                        "output_preview": (result.output or result.error or "")[:200],
                    })
            else:
                # Final assistant message – no tool calls
                assistant_msg = LLMMessage(
                    role="assistant",
                    content=assistant_content,
                )
                messages.append(assistant_msg)
                break

        return messages, tool_history
