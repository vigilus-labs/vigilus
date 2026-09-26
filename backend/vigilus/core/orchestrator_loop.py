"""The Vigilus orchestrator loop.

Calls the orchestrator LLM, runs the research / remember / delegation control
blocks it emits, feeds results back, and repeats until it gives a final
answer. Shared by every front door through ``core.turn`` — web chat, the
scheduler, and the channel gateway.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.delegation import execute_delegation, parse_delegation, strip_delegation
from vigilus.core.events import get_event_bus
from vigilus.core.sse import (
    EVT_DELEGATION_RESULT,
    EVT_DELEGATION_START,
    EVT_ERROR,
    EVT_TEXT_CHUNK,
    EVT_TEXT_DELTA,
    EVT_THINKING,
    StreamBridge,
)
from vigilus.core.stream_text import SafeTextStreamer
from vigilus.core.tasks import TaskCancelled, await_cancelled, get_task_registry
from vigilus.db.base import get_session_factory
from vigilus.db.models import Message, MessageRole
from vigilus.providers.base import LLMMessage, LLMResponse

logger = structlog.get_logger(__name__)
event_bus = get_event_bus()


def load_db_messages_as_llm(db_messages: list[Message]) -> list[LLMMessage]:
    """Convert DB Message objects to LLMMessage objects for the LLM provider."""
    llm_msgs = []
    for m in db_messages:
        content = m.content
        if m.role == MessageRole.user:
            llm_msgs.append(LLMMessage(role="user", content=str(content)))
        elif m.role == MessageRole.assistant:
            # Assistant message may contain delegation JSON or plain text
            if isinstance(content, dict) and content.get("delegation"):
                # Reconstruct as text (delegation was stored separately)
                text = content.get("text", "")
                llm_msgs.append(LLMMessage(role="assistant", content=text))
            else:
                llm_msgs.append(LLMMessage(role="assistant", content=str(content)))
        elif m.role == MessageRole.tool:
            # Tool message = delegation result. Feed it back as a *user*
            # message: there was no native tool_call before it, so strict
            # providers reject role="tool" without a tool_call_id.
            result_text = str(content)
            operator_name = m.operator_id or "operator"
            if isinstance(content, dict):
                operator_name = content.get("operator") or operator_name
                if "result" in content:
                    result_text = str(content.get("result", content))
            llm_msgs.append(
                LLMMessage(
                    role="user",
                    content=frame_delegation_result(operator_name, result_text),
                )
            )
    return llm_msgs


def frame_delegation_result(operator_name: str, result_text: str) -> str:
    """Wrap a delegation result so the LLM knows it's not user input."""
    return (
        f"[DELEGATION RESULT from {operator_name} — automated message, "
        f"not user input]\n{result_text}"
    )


async def run_orchestrator(
    llm_history: list[LLMMessage],
    provider: Any,
    system_prompt: str,
    *,
    db: AsyncSession,
    session_id: str | None = None,
    provider_id: str | None = None,
    provider_type: str | None = None,
    model: str | None = None,
    max_delegations: int = 5,
    bridge: StreamBridge | None = None,
    cancel_event: Any | None = None,  # asyncio.Event — stop when set
    unattended: bool = False,  # scheduled run — use longer JIT wait
) -> list[dict[str, Any]]:
    """Run the Vigilus orchestrator loop.

    Calls the LLM, checks for delegation JSON in the response.
    If found, executes the delegation, feeds the result back, loops.
    Returns a list of new DB message rows to persist:
      {role, content, operator_id}

    Each iteration may produce:
      - assistant message (with or without delegation)
      - tool result message (delegation output)

    If *bridge* is provided, SSE events are published to it for real-time
    streaming to the frontend.
    """
    new_messages: list[dict[str, Any]] = []
    history = list(llm_history)  # working copy

    # Research turns ({"search"}/{"fetch"}) don't consume the delegation budget,
    # so they get their own headroom on top of max_delegations.
    iteration = 0
    delegations_used = 0
    max_iterations = max_delegations + 6

    # Some models (reasoning models especially) occasionally return an empty
    # final message — e.g. the whole token budget went to reasoning. Track the
    # last delegation result so we can retry once and then fall back to it
    # instead of persisting an empty reply.
    empty_retry_used = False
    last_result_summary: str | None = None

    while iteration < max_iterations:
        iteration += 1
        if cancel_event is not None and cancel_event.is_set():
            logger.info("orchestrator.cancelled", session_id=session_id)
            new_messages.append(
                {
                    "role": "assistant",
                    "content": "⏹ Task cancelled — stopped before any further steps were taken.",
                }
            )
            if bridge:
                bridge.publish(EVT_ERROR, {"error": "Task cancelled by user."})
            break

        # Budget hard stop: once the monthly LLM spend cap is reached, no
        # further provider calls are made — report the stop instead.
        from vigilus.core.budget import turn_budget_stop

        budget_stop = await turn_budget_stop(db)
        if budget_stop:
            logger.warning("orchestrator.budget_stop", session_id=session_id)
            new_messages.append({"role": "assistant", "content": budget_stop})
            if bridge:
                bridge.publish(EVT_ERROR, {"error": budget_stop})
            break

        logger.info("orchestrator.iteration", iteration=iteration)

        if bridge:
            bridge.publish(EVT_THINKING, {"iteration": iteration})

        # Stream the reply as it is written. The raw stream also carries the
        # machine-only control blocks, so it goes through SafeTextStreamer,
        # which releases only text it knows is prose.
        streamer = SafeTextStreamer()

        async def _on_text(delta: str) -> None:
            safe = streamer.feed(delta)
            if safe and bridge:
                bridge.publish(EVT_TEXT_CHUNK, {"text": safe})

        try:
            from vigilus.config import get_settings

            response: LLMResponse = await await_cancelled(
                provider.complete_streaming(
                    messages=history,
                    system=system_prompt,
                    tools=None,  # Orchestrator has NO tools — only delegates
                    temperature=0.0,
                    on_text=_on_text if bridge else None,
                ),
                cancel_event,
                timeout=get_settings().llm_request_timeout_seconds,
            )
        except TaskCancelled:
            logger.info("orchestrator.cancelled_while_waiting", session_id=session_id)
            new_messages.append(
                {
                    "role": "assistant",
                    "content": "⏹ Task cancelled — stopped while waiting for the AI provider.",
                }
            )
            if bridge:
                bridge.publish(EVT_ERROR, {"error": "Task cancelled by user."})
            break
        except Exception as e:
            logger.error("orchestrator.llm_error", error=str(e))
            new_messages.append(
                {
                    "role": "assistant",
                    "content": f"Error communicating with the AI provider: {e}",
                }
            )
            if bridge:
                bridge.publish(EVT_ERROR, {"error": str(e)})
            break

        from vigilus.core.llm_usage import record_llm_usage
        from vigilus.db.models import UsageActorType

        await record_llm_usage(
            usage=response.usage or {},
            actor_type=UsageActorType.orchestrator,
            session_id=session_id,
            provider_id=provider_id,
            provider_type=provider_type,
            model=model or getattr(provider, "default_model", None),
        )

        response_text = response.content or ""

        # Persist any {"remember": ...} blocks the orchestrator emitted and
        # strip them from the visible reply.
        from vigilus.core.memory import parse_remember_blocks, save_memory

        response_text, remembered = parse_remember_blocks(response_text)
        for item in remembered:
            scope = item.get("scope", "global")
            if scope not in ("global", "orchestrator"):
                scope = "global"
            await save_memory(
                db,
                scope=scope,
                content=item["remember"],
                category=item.get("category"),
                source="vigilus",
            )
        if remembered:
            await db.commit()

        async def _publish_text(text: str) -> None:
            """Surface the orchestrator's user-facing prose (control blocks stripped)."""
            await event_bus.publish(
                "operator.stream",
                {
                    "event_type": "operator.stream",
                    "session_id": session_id,
                    "content": text,
                },
            )
            if bridge:
                bridge.publish(EVT_TEXT_DELTA, {"text": text})

        # ── Research blocks ({"search"}/{"fetch"}) ──────────
        # Vigilus may research before planning. If it emitted research blocks,
        # run them (as the Vigilus principal, through the RBAC/audit pipeline),
        # feed the framed results back, and loop — no delegation this turn.
        from vigilus.core.research import parse_research_blocks, run_research

        response_text, research_blocks = parse_research_blocks(response_text)
        if research_blocks:
            await _publish_text(response_text)
            new_messages.append({"role": "assistant", "content": response_text})
            factory = get_session_factory()
            async with factory() as research_db:
                research_results = await run_research(
                    research_blocks,
                    db=research_db,
                    bridge=bridge,
                    session_id=session_id,
                )
            new_messages.append(
                {
                    "role": "tool",
                    "content": {
                        "operator": "Vigilus",
                        "result": research_results,
                        "status": "success",
                    },
                    "operator_id": "Vigilus",
                }
            )
            history.append(LLMMessage(role="assistant", content=response_text))
            history.append(LLMMessage(role="user", content=research_results))
            continue

        # Check for delegation
        delegation = parse_delegation(response_text)

        # The delegation JSON is a machine-only control block. Strip it so the
        # user sees just the orchestrator's plain-text plan ("here's what I'll
        # do …"), which renders immediately while the operator works.
        visible_text = strip_delegation(response_text) if delegation else response_text

        if delegation is None:
            # Final response — no delegation
            final_text = visible_text.strip()
            if not final_text:
                if not empty_retry_used:
                    empty_retry_used = True
                    logger.warning("orchestrator.empty_response", iteration=iteration)
                    history.append(
                        LLMMessage(
                            role="user",
                            content=(
                                "[SYSTEM] Your previous reply was empty. Reply now with "
                                "your final answer for the user as plain text — summarize "
                                "what was done and the results. Do not delegate again."
                            ),
                        )
                    )
                    continue
                # Still empty after a retry — surface the last operator report
                # rather than persisting a blank message.
                logger.warning("orchestrator.empty_response_fallback")
                if last_result_summary:
                    final_text = (
                        "I couldn't generate a closing summary, so here is the "
                        "operator's report directly:\n\n" + last_result_summary
                    )
                else:
                    final_text = (
                        "⚠️ The model returned an empty reply. Please try again "
                        "or rephrase your request."
                    )
            await _publish_text(final_text)
            new_messages.append({"role": "assistant", "content": final_text})
            break

        await _publish_text(visible_text)

        # Delegation found — save the assistant message and execute it
        operator_name = delegation.get("delegate") or delegation.get("operator")
        task_desc = delegation.get("task", "")[:200]
        logger.info("orchestrator.delegating", to=operator_name, task=task_desc[:80])

        if bridge:
            bridge.publish(
                EVT_DELEGATION_START,
                {
                    "operator": operator_name,
                    "task": task_desc,
                },
            )

        # Reflect the current step in the live task registry (for the tasks view)
        if session_id:
            get_task_registry().update(
                session_id,
                step=f"Delegating to {operator_name}",
                operator=operator_name,
            )

        # Save the assistant message that contains the delegation request. The
        # stored text is the user-facing plan (JSON stripped); the parsed
        # delegation rides alongside it for history reconstruction.
        new_messages.append(
            {
                "role": "assistant",
                "content": {"text": visible_text, "delegation": delegation},
            }
        )

        # Execute the delegation
        await event_bus.publish(
            "action.created",
            {
                "event_type": "action.created",
                "action": "delegation_start",
                "operator": operator_name,
                "session_id": session_id,
            },
        )

        # Get a fresh DB session for delegation (it may run its own queries)
        factory = get_session_factory()
        try:
            async with factory() as del_db:
                delegation_result = await execute_delegation(
                    delegation,
                    db=del_db,
                    session_id=session_id,
                    bridge=bridge,
                    cancel_event=cancel_event,
                    unattended=unattended,
                )
        except TaskCancelled:
            logger.info("orchestrator.cancelled_while_delegating", session_id=session_id)
            new_messages.append(
                {
                    "role": "assistant",
                    "content": "⏹ Task cancelled — stopped while waiting for the AI provider.",
                }
            )
            if bridge:
                bridge.publish(EVT_ERROR, {"error": "Task cancelled by user."})
            break

        if cancel_event is not None and cancel_event.is_set():
            logger.info("orchestrator.cancelled_after_delegation", session_id=session_id)
            new_messages.append(
                {
                    "role": "assistant",
                    "content": "⏹ Task cancelled — stopped before any further steps were taken.",
                }
            )
            if bridge:
                bridge.publish(EVT_ERROR, {"error": "Task cancelled by user."})
            break

        await event_bus.publish(
            "action.completed",
            {
                "event_type": "action.completed",
                "action": "delegation_complete",
                "operator": operator_name,
                "status": delegation_result.get("status"),
                "session_id": session_id,
            },
        )

        # Format delegation result for the orchestrator
        result_summary = format_delegation_result(delegation_result)
        last_result_summary = result_summary

        if bridge:
            bridge.publish(
                EVT_DELEGATION_RESULT,
                {
                    "operator": operator_name,
                    "status": delegation_result.get("status"),
                    "loop_detected": delegation_result.get("loop_detected", False),
                    "iteration_limit_reached": delegation_result.get(
                        "iteration_limit_reached", False
                    ),
                    "summary": result_summary[:500],
                },
            )

        # Save the delegation result as a "tool" message for history
        new_messages.append(
            {
                "role": "tool",
                "content": {
                    "operator": operator_name,
                    "result": result_summary,
                    "status": delegation_result.get("status"),
                },
                "operator_id": operator_name,  # Track which operator produced this
            }
        )

        # Feed result back to history for next LLM call. The result goes in
        # as a framed user message — not role="tool" — because no native
        # tool_call preceded it and strict providers 400 on orphan tool
        # messages (missing tool_call_id).
        history.append(LLMMessage(role="assistant", content=response_text))
        history.append(
            LLMMessage(
                role="user",
                content=frame_delegation_result(operator_name, result_summary),
            )
        )

        delegations_used += 1
        if delegations_used > max_delegations:
            logger.info("orchestrator.max_delegations_reached", used=delegations_used)
            break

    return new_messages


def format_delegation_result(result: dict[str, Any]) -> str:
    """Format a delegation result dict into a readable string for the LLM."""
    status = result.get("status", "unknown")
    operator = result.get("operator", "unknown")

    if status == "error":
        return f"[Operator: {operator}] ERROR: {result.get('error', 'Unknown error')}"

    response = result.get("response", "")
    tool_calls = result.get("tool_calls", [])

    parts = [f"[Operator: {operator}] STATUS: {status}\n"]
    if result.get("loop_detected"):
        parts.append(
            "NOTE: this run was ABORTED by loop detection — the operator caught "
            "itself repeating an identical tool call. Re-delegating the exact "
            "same task unchanged will likely loop again; adjust the approach.\n"
        )
    if result.get("iteration_limit_reached"):
        parts.append(
            "NOTE: this run hit its iteration limit. The response is a summary "
            "of partial work, not a completed task. You may continue by "
            "delegating a narrower follow-up.\n"
        )
    if response:
        parts.append(f"RESPONSE:\n{response}\n")
    if tool_calls:
        parts.append("TOOLS USED:")
        for tc in tool_calls:
            parts.append(f"  - {tc.get('tool', 'unknown')}: {tc.get('output_preview', '')[:200]}")

    return "\n".join(parts)

