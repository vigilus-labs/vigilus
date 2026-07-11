"""Live task registry — tracks in-flight orchestrator turns so they can be
viewed and cancelled.

A "task" is one orchestrator turn (a single user message that fans out into
delegations and tool calls). Each running turn registers here with a
cooperative ``cancel_event``; the orchestrator and operator loops check it at
every iteration boundary and stop promptly when it is set.

This is process-local in-memory state — it reflects turns running inside *this*
backend process only.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Keep at most this many activity events per turn (most recent kept).
ACTIVITY_CAP = 300

T = TypeVar("T")


class TaskCancelled(Exception):
    """Raised when an in-flight turn is stopped by its user."""


async def await_cancelled(
    awaitable: Awaitable[T],
    cancel_event: asyncio.Event | None = None,
    *,
    timeout: float | None = None,
) -> T:
    """Await work while promptly honouring a running turn's cancellation.

    The awaited operation runs in its own task so a cancellation request can
    cancel a stalled provider request without cancelling the HTTP handler that
    owns the chat turn.  Callers must translate :class:`TaskCancelled` into a
    normal terminal turn result and run their usual cleanup.
    """
    if cancel_event is None:
        if timeout is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout=timeout)

    if cancel_event.is_set():
        raise TaskCancelled()

    operation = asyncio.ensure_future(awaitable)
    cancel_waiter = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait(
            {operation, cancel_waiter},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation in done:
            return await operation
        if cancel_waiter in done:
            operation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation
            raise TaskCancelled()

        operation.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await operation
        raise TimeoutError("Task operation timed out")
    finally:
        if not operation.done():
            operation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation
        cancel_waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_waiter


@dataclass
class RunningTask:
    """A single in-flight orchestrator turn."""

    session_id: str
    title: str
    id: str = field(default_factory=lambda: str(uuid4()))
    started_at: datetime = field(default_factory=_utcnow)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    current_step: str = "Starting…"
    operator: str | None = None
    # Buffered activity events so a client that navigated away and came back
    # can restore what the turn has been doing.
    activity: list[dict] = field(default_factory=list)

    @property
    def cancelling(self) -> bool:
        return self.cancel_event.is_set()

    def record(self, event_type: str, data: dict | None) -> None:
        self.activity.append(
            {
                "type": event_type,
                "data": data or {},
                "ts": _utcnow().isoformat(),
            }
        )
        if len(self.activity) > ACTIVITY_CAP:
            del self.activity[: len(self.activity) - ACTIVITY_CAP]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "title": self.title,
            "started_at": self.started_at.isoformat(),
            "elapsed_seconds": (_utcnow() - self.started_at).total_seconds(),
            "current_step": self.current_step,
            "operator": self.operator,
            "cancelling": self.cancelling,
        }

    def to_detail(self) -> dict:
        return {**self.to_dict(), "activity": list(self.activity)}


class TaskRegistry:
    """Singleton tracking running orchestrator turns, keyed by session_id."""

    _instance: TaskRegistry | None = None

    def __new__(cls) -> TaskRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tasks = {}
        return cls._instance

    _tasks: dict[str, RunningTask]

    def register(self, session_id: str, title: str) -> RunningTask:
        """Register a new running turn for *session_id*, replacing any prior one."""
        task = RunningTask(session_id=session_id, title=title or "Chat")
        self._tasks[session_id] = task
        logger.info("task.registered", session_id=session_id, task_id=task.id)
        return task

    def unregister(self, session_id: str, task_id: str | None = None) -> None:
        """Remove a completed task, without removing a newer replacement."""
        task = self._tasks.get(session_id)
        if task and (task_id is None or task.id == task_id):
            self._tasks.pop(session_id, None)

    def cancel(self, session_id: str) -> bool:
        """Request cooperative cancellation. Returns False when no task exists."""
        task = self._tasks.get(session_id)
        if not task:
            return False
        if not task.cancel_event.is_set():
            task.cancel_event.set()
            task.current_step = "Cancelling…"
            logger.info("task.cancel_requested", session_id=session_id, task_id=task.id)
        return True

    def is_cancelled(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and task.cancel_event.is_set())

    def update(
        self, session_id: str, *, step: str | None = None, operator: str | None = None
    ) -> None:
        task = self._tasks.get(session_id)
        if not task:
            return
        if step is not None:
            task.current_step = step
        if operator is not None:
            task.operator = operator

    def record(self, session_id: str, event_type: str, data: dict | None) -> None:
        task = self._tasks.get(session_id)
        if task:
            task.record(event_type, data)

    def get(self, session_id: str) -> RunningTask | None:
        return self._tasks.get(session_id)

    def list_running(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values()]


def get_task_registry() -> TaskRegistry:
    return TaskRegistry()
