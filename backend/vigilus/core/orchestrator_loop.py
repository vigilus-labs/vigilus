"""The Vigilus orchestrator loop.

Calls the orchestrator LLM and handles what it asks for: native tool calls
(delegate / research / remember — run in parallel where independent) on
providers with the capability flag, or the legacy parsed JSON control blocks
in its text on the ones without. Results feed back until the model gives a
final answer. Shared by every front door through ``core.turn`` — web chat,
the scheduler, and the channel gateway.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.delegation import execute_delegation, parse_delegation, strip_delegation
from vigilus.core.events import get_event_bus
from vigilus.core.orchestrator_tools import (
    DELEGATE_TOOL,
    execute_tool_batch,
    orchestrator_uses_native_tools,
)
from vigilus.core.sse import (
    EVT_DELEGATION_RESULT,
    EVT_DELEGATION_START,
    EVT_ERROR,
    EVT_TEXT_CHUNK,
    EVT_TEXT_DELTA,
    EVT_THINKING,
    StreamBridge,
)
from vigilus.core.stream_text import SafeTextStreamer, strip_control_blocks
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
            if isinstance(content, dict):
                if content.get("tool_calls"):
                    # Native tool-calling round trip: rebuild the assistant
                    # message with its tool_use blocks so the following tool
                    # messages pair up correctly.
                    llm_msgs.append(
                        LLMMessage(
                            role="assistant",
                            content=content.get("text", ""),
                            tool_calls=content.get("tool_calls"),
                        )
                    )
                elif content.get("delegation"):
                    # Legacy text-path delegation (reconstruct as text;
                    # the delegation was stored alongside)
                    text = content.get("text", "")
                    llm_msgs.append(LLMMessage(role="assistant", content=text))
                else:
                    llm_msgs.append(LLMMessage(role="assistant", content=str(content)))
            else:
                llm_msgs.append(LLMMessage(role="assistant", content=str(content)))
        elif m.role == MessageRole.tool:
            if isinstance(content, dict) and content.get("tool_use_id"):
                # Native tool result with a matching tool_use — send as a real
                # tool message so providers see a valid tool_use/tool_result
                # pair.
                result_text = str(content.get("result", content))
                llm_msgs.append(
                    LLMMessage(
                        role="tool",
                        name=content.get("tool") or content.get("operator"),
                        tool_use_id=content["tool_use_id"],
                        content=result_text,
                    )
                )
                continue
            # Legacy: tool message = delegation/research result fed back as a
            # *user* message — there was no native tool_call before it, so
            # strict providers reject role="tool" without a tool_call_id.
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
    cached_system: str | None = None,
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

    # Native tool delegation (issues #39/#40): providers with the capability
    # flag get real tools — delegate / web_search / web_fetch / remember — and
    # several delegate calls in one response run in parallel. Providers
    # without the flag (and any text control blocks) keep the parsed path.
    native_tools = orchestrator_uses_native_tools(provider)
    orchestrator_tool_specs = None
    if native_tools:
        from vigilus.config import get_settings
        from vigilus.core.orchestrator_tools import build_orchestrator_tools

        orchestrator_tool_specs = await build_orchestrator_tools(
            db, search_enabled=get_settings().search_enabled
        )

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
                    cached_system=cached_system,
                    cache_conversation=True,
                    tools=orchestrator_tool_specs,
                    temperature=0.0,
                    on_text=_on_text if bridge else None,
                    model=model,
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

        # ── Native tool calls (delegate / web_search / web_fetch / remember) ──
        # When the model answered with tool calls, everything it asked for in
        # this response is handled here — research and memory as tools, and
        # all delegate calls concurrently. There must be no `continue` past
        # this point without feeding tool results back: strict providers
        # reject a follow-up request whose tool_use blocks were never answered.
        if native_tools and response.tool_uses:
            visible_text = strip_control_blocks(response_text)
            if visible_text:
                await _publish_text(visible_text)

            delegate_args = [
                dict(tu.arguments or {})
                for tu in response.tool_uses
                if tu.name == DELEGATE_TOOL
            ]
            tool_call_dicts = [
                {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.arguments or {}}
                for tu in response.tool_uses
            ]
            new_messages.append(
                {
                    "role": "assistant",
                    "content": {
                        "text": visible_text,
                        "delegation": delegate_args,
                        "tool_calls": tool_call_dicts,
                    },
                }
            )
            history.append(
                LLMMessage(role="assistant", content=visible_text, tool_calls=tool_call_dicts)
            )

            if session_id and len(response.tool_uses) > 1:
                get_task_registry().update(
                    session_id,
                    step=f"Running {len(response.tool_uses)} parallel steps",
                )

            from vigilus.config import get_settings

            settings = get_settings()
            batch = await execute_tool_batch(
                response.tool_uses,
                session_id=session_id,
                bridge=bridge,
                cancel_event=cancel_event,
                unattended=unattended,
                remaining_delegations=max_delegations - delegations_used,
                max_parallel=settings.max_parallel_delegations,
            )

            saw_cancel = False
            for tool_use, tool_res in zip(response.tool_uses, batch):
                if isinstance(tool_res.meta.get("exception"), TaskCancelled):
                    saw_cancel = True
                    continue

                if tool_res.is_delegation:
                    delegation_result = tool_res.meta["delegation_result"]
                    operator_name = tool_res.operator or "unknown"
                    result_summary = format_delegation_result(delegation_result)
                    last_result_summary = result_summary
                    delegations_used += 1
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
                    new_messages.append(
                        {
                            "role": "tool",
                            "content": {
                                "operator": operator_name,
                                "result": result_summary,
                                "status": delegation_result.get("status"),
                                "tool": DELEGATE_TOOL,
                                "tool_use_id": tool_res.tool_use_id,
                            },
                            "operator_id": operator_name,
                        }
                    )
                else:
                    # Research, memory, unknown tools, and budget-refused
                    # delegations all report back through this branch.
                    attribution = tool_res.operator or "Vigilus"
                    new_messages.append(
                        {
                            "role": "tool",
                            "content": {
                                "operator": attribution,
                                "result": tool_res.text,
                                "status": "success" if tool_res.ok else "error",
                                "tool": tool_res.name,
                                "tool_use_id": tool_res.tool_use_id,
                            },
                            "operator_id": attribution,
                        }
                    )
                history.append(
                    LLMMessage(
                        role="tool",
                        name=tool_res.name,
                        tool_use_id=tool_res.tool_use_id,
                        content=tool_res.text,
                    )
                )

            if saw_cancel:
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

            # Budget-capped delegate calls were refused inside the batch, so
            # the model sees why nothing happened and can conclude instead.
            continue

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

