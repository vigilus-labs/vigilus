"""In-process async event bus for decoupled pub/sub communication."""

from __future__ import annotations

import asyncio
import enum
from collections import defaultdict
from typing import Any, Callable, Coroutine

import structlog

logger = structlog.get_logger(__name__)


class EventType(str, enum.Enum):
    """Well-known event types emitted by Vigilus subsystems."""

    ACTION_CREATED = "action.created"
    ACTION_UPDATED = "action.updated"
    JIT_REQUESTED = "jit.requested"
    JIT_RESOLVED = "jit.resolved"
    OPERATOR_STREAM = "operator.stream"
    SERVER_STATUS_CHANGED = "server.status_changed"
    MCP_SERVER_STATUS = "mcp.server_status"
    SESSION_UPDATED = "session.updated"


# Callback type: async def handler(payload: dict[str, Any]) -> None
EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """Simple in-process async event bus.

    Subscribers register async callbacks for specific event types.
    When an event is published, all matching callbacks are dispatched
    concurrently via ``asyncio.create_task``.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventCallback]] = defaultdict(list)

    def subscribe(self, event_type: str | EventType, callback: EventCallback) -> None:
        """Register *callback* to be invoked when *event_type* is published."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        self._subscribers[key].append(callback)
        logger.debug("event_bus.subscribe", event_type=key)

    def unsubscribe(self, event_type: str | EventType, callback: EventCallback) -> None:
        """Remove a previously registered *callback*."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        try:
            self._subscribers[key].remove(callback)
        except ValueError:
            pass

    async def publish(self, event_type: str | EventType, payload: dict[str, Any] | None = None) -> None:
        """Publish an event, dispatching to all registered callbacks."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        payload = payload or {}
        callbacks = self._subscribers.get(key, [])
        if not callbacks:
            return

        logger.debug("event_bus.publish", event_type=key, subscriber_count=len(callbacks))

        tasks = []
        for cb in callbacks:
            tasks.append(asyncio.create_task(self._safe_invoke(cb, key, payload)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _safe_invoke(
        callback: EventCallback,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Invoke a callback, catching and logging any exceptions."""
        try:
            await callback(payload)
        except Exception:
            logger.exception("event_bus.callback_error", event_type=event_type)


# ── Singleton ───────────────────────────────────────────────

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the global singleton EventBus instance."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
