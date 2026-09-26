"""Chat API – sessions, messages, the WebSocket event feed, and the SSE stream.

The orchestrator loop lives in ``core.orchestrator_loop`` and turns run via
``core.turn`` (shared with the scheduler and the channel gateway); this
module owns only the HTTP side.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.events import get_event_bus
from vigilus.core.orchestrator import OrchestratorNotConfigured
from vigilus.core.sse import (
    EVT_DELEGATION_RESULT,
    EVT_DELEGATION_START,
    EVT_DONE,
    EVT_ERROR,
    EVT_JIT_REQUEST,
    EVT_TEXT_DELTA,
    EVT_THINKING,
    EVT_TOOL_CALL,
    EVT_TOOL_RESULT,
    StreamBridge,
    register_bridge,
    unregister_bridge,
)
from vigilus.core.tasks import get_task_registry
from vigilus.core.turn import execute_turn, turn_title
from vigilus.db.base import get_db
from vigilus.db.models import ChannelChat, Message, Operator, Session
from vigilus.schemas.chat import (
    MessageCreate,
    MessageResponse,
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)

router = APIRouter(tags=["Chat"])
logger = structlog.get_logger(__name__)
event_bus = get_event_bus()


# ── WebSocket ──────────────────────────────────────────────


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    from vigilus.api.deps import bearer_token
    from vigilus.config import get_settings
    from vigilus.core.auth import decode_token

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
        "action.created",
        "action.updated",
        "action.completed",
        "jit.requested",
        "jit.resolved",
        "operator.stream",
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

# How long the stream endpoint waits for a turn's bridge to be registered
# before concluding that no turn is running.
_BRIDGE_WAIT_SECONDS = 5.0
_BRIDGE_POLL_SECONDS = 0.05


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str):
    """SSE endpoint for streaming orchestrator activity to the frontend.

    The frontend connects to this endpoint after sending a message.
    Events flow until the bridge is closed (turn completes or errors).
    """
    from vigilus.core.sse import get_bridge

    # The frontend opens this stream right after POSTing its message, so the
    # bridge may not exist yet — the POST handler still has to build the prompt
    # and (sometimes) compress the context first. Giving up on the first miss
    # would drop the whole turn's live feed, including the orchestrator's plan
    # message, leaving the user staring at a spinner until the turn finished.
    bridge = get_bridge(session_id)
    waited = 0.0
    while bridge is None and waited < _BRIDGE_WAIT_SECONDS:
        await asyncio.sleep(_BRIDGE_POLL_SECONDS)
        waited += _BRIDGE_POLL_SECONDS
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


async def _detect_mentioned_operators(content: str, db: AsyncSession) -> list[str]:
    """Return names of enabled operators explicitly @-mentioned in *content*.

    Operator names can contain spaces, so we match each known name against the
    text directly (case-insensitive) rather than tokenizing. Longer names are
    checked first so "@Infrastructure Operator" doesn't also match a shorter
    "@Operator". Returns names in the order they appear in the message.
    """
    if not content or "@" not in content:
        return []

    operators = (
        (
            await db.execute(
                select(Operator).where(
                    Operator.enabled == True, Operator.delegatable == True  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )

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


async def _mention_system_extra(content: str, db: AsyncSession) -> str | None:
    """System-prompt addition pinning delegation to @-mentioned operators."""
    mentioned = await _detect_mentioned_operators(content, db)
    if not mentioned:
        return None
    tagged = ", ".join(f'"{name}"' for name in mentioned)
    return (
        "## Explicit operator selection\n\n"
        f"The user's latest message tags specific operators with @mentions: {tagged}. "
        "Delegate the task to the tagged operator(s) exactly — and in that order if "
        "there is more than one — using the normal delegation format. Do NOT substitute "
        "a different operator or skip the delegation, even if another operator seems "
        "better suited; the user has chosen deliberately. If a tagged operator cannot "
        "do the task, report that back rather than silently picking another."
    )


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

    # One turn at a time per session: a second message while a turn is still
    # running would interleave history and cancel state. 409 tells the client
    # to wait (or cancel the running turn) first.
    if get_task_registry().get(session.id) is not None:
        raise HTTPException(
            status_code=409,
            detail="A task is already running in this session — wait for it to "
            "finish or cancel it before sending another message.",
        )

    # ── Register this turn so it can be viewed, restored, and cancelled ───
    # Registered before the turn runs, so title it the way the turn will.
    running_task = get_task_registry().register(
        session.id, turn_title(session, data.content) or "Chat"
    )

    # Buffer activity-feed events on the task so a client that navigates away
    # and returns can restore what the turn has been doing.
    activity_events = {
        EVT_THINKING,
        EVT_DELEGATION_START,
        EVT_TOOL_CALL,
        EVT_TOOL_RESULT,
        EVT_DELEGATION_RESULT,
        EVT_TEXT_DELTA,
        EVT_ERROR,
        "loop_detected",
    }

    def _record_activity(event: str, data: dict) -> None:
        if event in activity_events:
            get_task_registry().record(session.id, event, data)

    # ── Create SSE bridge for streaming ───────────────────
    bridge = StreamBridge(on_event=_record_activity)
    register_bridge(session.id, bridge)

    # Forward JIT approval requests raised during this turn into the
    # chat stream so the user can approve inline without leaving the page.
    async def _forward_jit(payload: dict) -> None:
        bridge.publish(EVT_JIT_REQUEST, payload or {})

    event_bus.subscribe("jit.requested", _forward_jit)

    # ── Run the turn (stream events via bridge) ───────────
    try:
        result = await execute_turn(
            db,
            session,
            data.content,
            bridge=bridge,
            cancel_event=running_task.cancel_event,
            # Honor explicit @operator mentions: delegate to exactly those.
            system_extra=await _mention_system_extra(data.content, db),
        )
        bridge.publish(
            EVT_DONE,
            {
                "session_id": session.id,
                "message_id": result.assistant_message.id if result.assistant_message else None,
            },
        )
    except OrchestratorNotConfigured as e:
        bridge.publish(EVT_ERROR, {"error": str(e)})
        bridge.publish(EVT_DONE, {"session_id": session.id})
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("orchestrator.run_failed", error=str(e), session_id=session.id)
        bridge.publish(EVT_ERROR, {"error": str(e)})
        bridge.publish(EVT_DONE, {"session_id": session.id})
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        event_bus.unsubscribe("jit.requested", _forward_jit)
        get_task_registry().unregister(session.id, running_task.id)
        bridge.close()
        unregister_bridge(session.id)

    # Fallback: return the user message if the turn produced no assistant row
    return _message_to_response(result.assistant_message or result.user_message)
