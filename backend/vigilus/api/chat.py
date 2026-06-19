"""Chat API – sessions, messages, WebSocket, and the Vigilus orchestrator loop.

The Vigilus orchestrator is NOT an Operator. It has its own provider/model
configured via /api/orchestrator and lives on the /chat page. Its only job is
to receive user messages and delegate work to specialist Operators.
"""

from __future__ import annotations

import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

import structlog

from vigilus.db.base import get_db, get_session_factory
from vigilus.db.models import ChannelChat, Session, Message, Operator, OperatorTool, MessageRole
from vigilus.schemas.chat import (
    SessionCreate,
    SessionResponse,
    MessageCreate,
    MessageResponse,
    SessionUpdate,
)
from vigilus.core.events import get_event_bus
from vigilus.core.orchestrator import (
    OrchestratorNotConfigured,
    load_orchestrator_config,
    resolve_orchestrator_provider,
)
from vigilus.core.delegation import parse_delegation, strip_delegation, execute_delegation
from vigilus.core.prompt_builder import PromptBuilder
from vigilus.core.tasks import get_task_registry
from vigilus.core.compressor import ContextCompressor, estimate_tokens
from vigilus.providers.base import LLMMessage, LLMResponse, ToolSpec, ToolUse
from vigilus.api.sse import (
    StreamBridge,
    register_bridge,
    unregister_bridge,
    EVT_THINKING,
    EVT_DELEGATION_START,
    EVT_DELEGATION_RESULT,
    EVT_TOOL_CALL,
    EVT_TOOL_RESULT,
    EVT_TEXT_DELTA,
    EVT_JIT_REQUEST,
    EVT_DONE,
    EVT_ERROR,
)

router = APIRouter(tags=["Chat"])
logger = structlog.get_logger(__name__)
event_bus = get_event_bus()


# ── WebSocket ──────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    from vigilus.config import get_settings
    from vigilus.core.auth import decode_token
    from vigilus.api.deps import bearer_token

    # JWT signature + expiry is sufficient here; token_version DB check is skipped
    # intentionally to keep the hot-path lock-free for the event stream.
    # Browsers send the cookie; the TUI sends an Authorization header.
    token = websocket.cookies.get(get_settings().auth_cookie_name) or bearer_token(
        websocket.headers.get("authorization")
    )
    payload = decode_token(token) if token else None
    if payload is None:
        # Must accept the WS handshake before we can send a close frame with a
        # custom code. Closing before accept() makes Starlette return HTTP 403,
        # which the browser sees as code 1006 — not the 4401 the frontend keys on.
        await websocket.accept()
        await websocket.close(code=4401, reason="Not authenticated")
        return

    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    callbacks: list[tuple[str, Any]] = []

    def _make_handler(event_type: str):
        async def _handler(payload: Any):
            await queue.put({"type": event_type, "payload": payload or {}})
        return _handler

    ws_events = [
        "action.created", "action.updated", "action.completed",
        "jit.requested", "jit.resolved", "operator.stream",
    ]
    for evt in ws_events:
        handler = _make_handler(evt)
        event_bus.subscribe(evt, handler)
        callbacks.append((evt, handler))

    try:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("ws_error", error=str(e))
    finally:
        for evt, handler in callbacks:
            event_bus.unsubscribe(evt, handler)


# ── SSE Streaming ──────────────────────────────────────────


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str):
    """SSE endpoint for streaming orchestrator activity to the frontend.

    The frontend connects to this endpoint after sending a message.
    Events flow until the bridge is closed (turn completes or errors).
    """
    from vigilus.api.sse import get_bridge

    bridge = get_bridge(session_id)
    if not bridge:
        # No active turn — return a done event immediately
        async def _empty():
            yield "event: done\ndata: {}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    return StreamingResponse(
        bridge.aiter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Helpers ────────────────────────────────────────────────


def _session_to_response(session: Session) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        title=session.title,
        operator_context=session.operator_context,
        operator_id=session.operator_id,
        origin=session.origin,
        created_at=session.created_at,
        last_active_at=session.last_active_at,
    )


def _message_to_response(msg: Message) -> MessageResponse:
    """Convert a DB Message to a response, parsing JSON content if needed."""
    content = msg.content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                content = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return MessageResponse(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role.value,
        content=content,
        operator_id=msg.operator_id,
        created_at=msg.created_at,
    )


def _load_db_messages_as_llm(db_messages: list[Message]) -> list[LLMMessage]:
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
            llm_msgs.append(LLMMessage(
                role="user",
                content=_frame_delegation_result(operator_name, result_text),
            ))
    return llm_msgs


def _frame_delegation_result(operator_name: str, result_text: str) -> str:
    """Wrap a delegation result so the LLM knows it's not user input."""
    return (
        f"[DELEGATION RESULT from {operator_name} — automated message, "
        f"not user input]\n{result_text}"
    )


async def _detect_mentioned_operators(content: str, db: AsyncSession) -> list[str]:
    """Return names of enabled operators explicitly @-mentioned in *content*.

    Operator names can contain spaces, so we match each known name against the
    text directly (case-insensitive) rather than tokenizing. Longer names are
    checked first so "@Infrastructure Operator" doesn't also match a shorter
    "@Operator". Returns names in the order they appear in the message.
    """
    if not content or "@" not in content:
        return []

    from vigilus.db.models import Operator

    operators = (await db.execute(
        select(Operator).where(
            Operator.enabled == True, Operator.delegatable == True  # noqa: E712
        )
    )).scalars().all()

    lower = content.lower()
    found: list[tuple[int, str]] = []
    consumed_spans: list[tuple[int, int]] = []
    for op in sorted(operators, key=lambda o: len(o.name), reverse=True):
        needle = f"@{op.name.lower()}"
        start = lower.find(needle)
        while start != -1:
            end = start + len(needle)
            # Skip if this span overlaps a longer name we already matched.
            if not any(s < end and start < e for s, e in consumed_spans):
                found.append((start, op.name))
                consumed_spans.append((start, end))
            start = lower.find(needle, end)

    return [name for _, name in sorted(found, key=lambda x: x[0])]


# ── Orchestrator Loop ──────────────────────────────────────


async def _run_orchestrator(
    llm_history: list[LLMMessage],
    provider: Any,
    system_prompt: str,
    *,
    db: AsyncSession,
    session_id: str | None = None,
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

    while iteration < max_iterations:
        iteration += 1
        if cancel_event is not None and cancel_event.is_set():
            logger.info("orchestrator.cancelled", session_id=session_id)
            new_messages.append({
                "role": "assistant",
                "content": "⏹ Task cancelled — stopped before any further steps were taken.",
            })
            if bridge:
                bridge.publish(EVT_ERROR, {"error": "Task cancelled by user."})
            break

        logger.info("orchestrator.iteration", iteration=iteration)

        if bridge:
            bridge.publish(EVT_THINKING, {"iteration": iteration})

        try:
            response: LLMResponse = await provider.complete(
                messages=history,
                system=system_prompt,
                tools=None,  # Orchestrator has NO tools — only delegates
                temperature=0.0,
            )
        except Exception as e:
            logger.error("orchestrator.llm_error", error=str(e))
            new_messages.append({
                "role": "assistant",
                "content": f"Error communicating with the AI provider: {e}",
            })
            if bridge:
                bridge.publish(EVT_ERROR, {"error": str(e)})
            break

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
            await event_bus.publish("operator.stream", {
                "event_type": "operator.stream",
                "session_id": session_id,
                "content": text,
            })
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
                    research_blocks, db=research_db, bridge=bridge, session_id=session_id,
                )
            new_messages.append({
                "role": "tool",
                "content": {"operator": "Vigilus", "result": research_results, "status": "success"},
                "operator_id": "Vigilus",
            })
            history.append(LLMMessage(role="assistant", content=response_text))
            history.append(LLMMessage(role="user", content=research_results))
            continue

        # Check for delegation
        delegation = parse_delegation(response_text)

        # The delegation JSON is a machine-only control block. Strip it so the
        # user sees just the orchestrator's plain-text plan ("here's what I'll
        # do …"), which renders immediately while the operator works.
        visible_text = strip_delegation(response_text) if delegation else response_text
        await _publish_text(visible_text)

        if delegation is None:
            # Final response — no delegation
            new_messages.append({"role": "assistant", "content": visible_text})
            break

        # Delegation found — save the assistant message and execute it
        operator_name = delegation.get("delegate") or delegation.get("operator")
        task_desc = delegation.get("task", "")[:200]
        logger.info("orchestrator.delegating", to=operator_name, task=task_desc[:80])

        if bridge:
            bridge.publish(EVT_DELEGATION_START, {
                "operator": operator_name,
                "task": task_desc,
            })

        # Reflect the current step in the live task registry (for the tasks view)
        if session_id:
            get_task_registry().update(
                session_id, step=f"Delegating to {operator_name}", operator=operator_name,
            )

        # Save the assistant message that contains the delegation request. The
        # stored text is the user-facing plan (JSON stripped); the parsed
        # delegation rides alongside it for history reconstruction.
        new_messages.append({
            "role": "assistant",
            "content": {"text": visible_text, "delegation": delegation},
        })

        # Execute the delegation
        await event_bus.publish("action.created", {
            "event_type": "action.created",
            "action": "delegation_start",
            "operator": operator_name,
            "session_id": session_id,
        })

        # Get a fresh DB session for delegation (it may run its own queries)
        factory = get_session_factory()
        async with factory() as del_db:
            delegation_result = await execute_delegation(
                delegation, db=del_db, session_id=session_id,
                bridge=bridge, cancel_event=cancel_event, unattended=unattended,
            )

        await event_bus.publish("action.completed", {
            "event_type": "action.completed",
            "action": "delegation_complete",
            "operator": operator_name,
            "status": delegation_result.get("status"),
            "session_id": session_id,
        })

        # Format delegation result for the orchestrator
        result_summary = _format_delegation_result(delegation_result)

        if bridge:
            bridge.publish(EVT_DELEGATION_RESULT, {
                "operator": operator_name,
                "status": delegation_result.get("status"),
                "summary": result_summary[:500],
            })

        # Save the delegation result as a "tool" message for history
        new_messages.append({
            "role": "tool",
            "content": {
                "operator": operator_name,
                "result": result_summary,
                "status": delegation_result.get("status"),
            },
            "operator_id": operator_name,  # Track which operator produced this
        })

        # Feed result back to history for next LLM call. The result goes in
        # as a framed user message — not role="tool" — because no native
        # tool_call preceded it and strict providers 400 on orphan tool
        # messages (missing tool_call_id).
        history.append(LLMMessage(role="assistant", content=response_text))
        history.append(LLMMessage(
            role="user",
            content=_frame_delegation_result(operator_name, result_summary),
        ))

        delegations_used += 1
        if delegations_used > max_delegations:
            logger.info("orchestrator.max_delegations_reached", used=delegations_used)
            break

    return new_messages


def _format_delegation_result(result: dict[str, Any]) -> str:
    """Format a delegation result dict into a readable string for the LLM."""
    status = result.get("status", "unknown")
    operator = result.get("operator", "unknown")

    if status == "error":
        return f"[Operator: {operator}] ERROR: {result.get('error', 'Unknown error')}"

    response = result.get("response", "")
    tool_calls = result.get("tool_calls", [])

    parts = [f"[Operator: {operator}] STATUS: {status}\n"]
    if response:
        parts.append(f"RESPONSE:\n{response}\n")
    if tool_calls:
        parts.append("TOOLS USED:")
        for tc in tool_calls:
            parts.append(f"  - {tc.get('tool', 'unknown')}: {tc.get('output_preview', '')[:200]}")

    return "\n".join(parts)


# ── REST Endpoints ─────────────────────────────────────────


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Session).order_by(Session.last_active_at.desc()))
    return [_session_to_response(s) for s in result.scalars().all()]


@router.post("/sessions", response_model=SessionResponse)
async def create_session(data: SessionCreate, db: AsyncSession = Depends(get_db)):
    session = Session(
        title=data.title or "New Chat",
        operator_id=data.operator_id,
        origin="web",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _session_to_response(session)


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(session_id: str, data: SessionUpdate, db: AsyncSession = Depends(get_db)):
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if data.title is not None:
        session.title = data.title
    if data.operator_id is not None:
        session.operator_id = data.operator_id
    await db.commit()
    await db.refresh(session)
    return _session_to_response(session)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # Remove any channel link first: SQLite doesn't enforce ON DELETE CASCADE,
    # so leaving it would orphan the channel_chats row and break the next
    # inbound message for that chat (UNIQUE collision on re-create).
    await db.execute(sa_delete(ChannelChat).where(ChannelChat.session_id == session_id))
    await db.delete(session)
    await db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    )
    return [_message_to_response(m) for m in result.scalars().all()]


@router.post("/sessions/{session_id}/messages", response_model=MessageResponse)
async def send_message(session_id: str, data: MessageCreate, db: AsyncSession = Depends(get_db)):
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # ── Resolve orchestrator provider ──────────────────────
    orch_cfg = load_orchestrator_config()
    try:
        provider, provider_row, model = await resolve_orchestrator_provider(db)
    except OrchestratorNotConfigured as e:
        raise HTTPException(status_code=500, detail=str(e))

    # ── Build system prompt (three-tier) ──────────────────
    prompt_builder = PromptBuilder(
        db=db, custom_identity=orch_cfg.custom_identity, soul=orch_cfg.soul,
    )
    system_prompt_obj = await prompt_builder.build(session_id=session.id)
    system_prompt = system_prompt_obj.render()

    # ── Honor explicit @operator mentions ─────────────────
    # When the user tags specific operators, direct the orchestrator to
    # delegate to exactly those, in order, rather than choosing its own.
    mentioned = await _detect_mentioned_operators(data.content, db)
    if mentioned:
        tagged = ", ".join(f'"{name}"' for name in mentioned)
        system_prompt += (
            "\n\n## Explicit operator selection\n\n"
            f"The user's latest message tags specific operators with @mentions: {tagged}. "
            "Delegate the task to the tagged operator(s) exactly — and in that order if "
            "there is more than one — using the normal delegation format. Do NOT substitute "
            "a different operator or skip the delegation, even if another operator seems "
            "better suited; the user has chosen deliberately. If a tagged operator cannot "
            "do the task, report that back rather than silently picking another."
        )

    # ── Save user message ─────────────────────────────────
    user_msg = Message(session_id=session.id, role=MessageRole.user, content=data.content)
    db.add(user_msg)

    # Auto-title: an untitled chat takes its name from the first message
    if not session.title or session.title == "New Chat":
        first_line = data.content.strip().splitlines()[0] if data.content.strip() else ""
        if first_line:
            session.title = first_line[:57] + "…" if len(first_line) > 60 else first_line

    await db.commit()

    # ── Build LLM history ─────────────────────────────────
    history = await db.execute(
        select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
    )
    llm_history = _load_db_messages_as_llm(list(history.scalars().all()))

    # ── Context compression ───────────────────────────────
    # Estimate system prompt tokens for the compressor budget
    system_tokens = len(system_prompt) // 4  # rough char→token estimate
    compressor = ContextCompressor(provider=provider, model=model)
    llm_history, compression_summary = await compressor.compress_if_needed(
        llm_history, system_tokens=system_tokens,
    )
    if compression_summary:
        logger.info("chat.compressed", session_id=session.id)
        # Rebuild volatile tier with compression notice
        system_prompt_obj = await prompt_builder.rebuild_volatile(
            system_prompt_obj,
            memory_context=f"[Previous conversation was compressed. Summary:]\n{compression_summary}",
        )
        system_prompt = system_prompt_obj.render()

    # ── Register this turn so it can be viewed, restored, and cancelled ───
    running_task = get_task_registry().register(session.id, session.title or "Chat")

    # Buffer activity-feed events on the task so a client that navigates away
    # and returns can restore what the turn has been doing.
    _ACTIVITY_EVENTS = {
        EVT_THINKING, EVT_DELEGATION_START, EVT_TOOL_CALL, EVT_TOOL_RESULT,
        EVT_DELEGATION_RESULT, EVT_TEXT_DELTA, EVT_ERROR,
    }

    def _record_activity(event: str, data: dict) -> None:
        if event in _ACTIVITY_EVENTS:
            get_task_registry().record(session.id, event, data)

    # ── Create SSE bridge for streaming ───────────────────
    bridge = StreamBridge(on_event=_record_activity)
    register_bridge(session.id, bridge)

    # Forward JIT approval requests raised during this turn into the
    # chat stream so the user can approve inline without leaving the page.
    async def _forward_jit(payload: dict) -> None:
        bridge.publish(EVT_JIT_REQUEST, payload or {})

    event_bus.subscribe("jit.requested", _forward_jit)

    # ── Run orchestrator (stream events via bridge) ───────
    try:
        new_msgs = await _run_orchestrator(
            llm_history,
            provider,
            system_prompt,
            db=db,
            session_id=session.id,
            bridge=bridge,
            cancel_event=running_task.cancel_event,
        )

        # ── Persist new messages (before closing the bridge so the
        #    final done event can carry the message id) ─────
        last_assistant = None
        for msg_data in new_msgs:
            role = MessageRole(msg_data["role"])
            db_msg = Message(
                session_id=session.id,
                role=role,
                content=msg_data["content"],
                operator_id=msg_data.get("operator_id"),
            )
            db.add(db_msg)
            if role == MessageRole.assistant:
                last_assistant = db_msg  # Track for return value

        await db.commit()
        if last_assistant:
            await db.refresh(last_assistant)

        bridge.publish(EVT_DONE, {
            "session_id": session.id,
            "message_id": last_assistant.id if last_assistant else None,
        })
    except Exception as e:
        logger.exception("orchestrator.run_failed", error=str(e), session_id=session.id)
        bridge.publish(EVT_ERROR, {"error": str(e)})
        bridge.publish(EVT_DONE, {"session_id": session.id})
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        event_bus.unsubscribe("jit.requested", _forward_jit)
        get_task_registry().unregister(session.id)
        bridge.close()
        unregister_bridge(session.id)

    if last_assistant:
        return _message_to_response(last_assistant)

    # Fallback: return user message
    return _message_to_response(user_msg)
