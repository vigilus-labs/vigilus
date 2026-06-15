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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Keep at most this many activity events per turn (most recent kept).
ACTIVITY_CAP = 300


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
        self.activity.append({
            "type": event_type,
            "data": data or {},
            "ts": _utcnow().isoformat(),
        })
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

    _instance: "TaskRegistry | None" = None

    def __new__(cls) -> "TaskRegistry":
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

    def unregister(self, session_id: str) -> None:
        self._tasks.pop(session_id, None)

    def cancel(self, session_id: str) -> bool:
        """Request cancellation. Returns True if a task was found and signalled."""
        task = self._tasks.get(session_id)
        if not task:
            return False
        task.cancel_event.set()
        logger.info("task.cancel_requested", session_id=session_id, task_id=task.id)
        return True

    def is_cancelled(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and task.cancel_event.is_set())

    def update(self, session_id: str, *, step: str | None = None, operator: str | None = None) -> None:
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
