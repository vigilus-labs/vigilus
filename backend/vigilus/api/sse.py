"""Server-Sent Events (SSE) helpers for streaming chat activity.

The chat streaming endpoint uses SSE to push real-time activity events
to the frontend while the orchestrator loop is running.  This replaces
the "dead silence" blocking approach with a live activity feed.

Event format::

    event: thinking
    data: {"iteration": 1}

    event: delegation_start
    data: {"operator": "Security Monitor", "task": "Check CVE-2024-1234"}

    event: tool_call
    data: {"tool": "wazuh_list_alerts", "operator": "Security Monitor"}

    event: tool_result
    data: {"tool": "wazuh_list_alerts", "preview": "Found 3 alerts..."}

    event: text_delta
    data: {"text": "I found "}

    event: delegation_result
    data: {"operator": "Security Monitor", "status": "success", "summary": "Found 3..."}

    event: done
    data: {"message_id": "abc-123"}
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Per-session stream registry ──────────────────────────────────────────
# Maps session_id → StreamBridge so the SSE endpoint can pick up the
# bridge created by the orchestrator loop.

_active_bridges: dict[str, StreamBridge] = {}


def register_bridge(session_id: str, bridge: StreamBridge) -> None:
    """Register a bridge for a session (called by the orchestrator loop)."""
    _active_bridges[session_id] = bridge


def unregister_bridge(session_id: str) -> None:
    """Remove a bridge after the turn completes."""
    _active_bridges.pop(session_id, None)


def get_bridge(session_id: str) -> StreamBridge | None:
    """Look up the active bridge for a session."""
    return _active_bridges.get(session_id)


# ── Event types ──────────────────────────────────────────────────────────

# Orchestrator is calling the LLM
EVT_THINKING = "thinking"

# Delegating to an operator
EVT_DELEGATION_START = "delegation_start"

# Operator is calling a tool
EVT_TOOL_CALL = "tool_call"

# Tool returned a result
EVT_TOOL_RESULT = "tool_result"

# Delegation completed
EVT_DELEGATION_RESULT = "delegation_result"

# Streaming LLM text chunk
EVT_TEXT_DELTA = "text_delta"

# A JIT approval request was created during this turn
EVT_JIT_REQUEST = "jit_request"

# Turn complete
EVT_DONE = "done"

# Error during the turn
EVT_ERROR = "error"


# ── SSE Event ────────────────────────────────────────────────────────────


@dataclass
class SSEEvent:
    """A single SSE event."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> str:
        """Encode as an SSE wire-format string."""
        return f"event: {self.event}\ndata: {json.dumps(self.data)}\n\n"


# ── Stream bridge ────────────────────────────────────────────────────────


class StreamBridge:
    """Bridge between the orchestrator loop and the SSE endpoint.

    The orchestrator publishes events to this bridge via ``publish()``.
    The SSE endpoint consumes them via ``aiter()``.

    Thread-safe via asyncio primitives.  Closes automatically when
    ``close()`` is called (on turn completion or error).
    """

    def __init__(self, on_event=None) -> None:
        self._queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue()
        self._closed = False
        # Optional sync callback (event, data) invoked on every publish — used
        # to buffer activity so a reconnecting client can restore it.
        self._on_event = on_event

    def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Publish an event (non-blocking, safe to call from async context)."""
        if self._closed:
            return
        self._queue.put_nowait(SSEEvent(event=event, data=data or {}))
        if self._on_event is not None:
            try:
                self._on_event(event, data or {})
            except Exception:  # never let buffering break the stream
                pass

    def close(self) -> None:
        """Signal end of stream."""
        self._closed = True
        self._queue.put_nowait(None)

    async def aiter(self) -> AsyncIterator[str]:
        """Yield SSE-encoded strings until the bridge is closed."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event.encode()
