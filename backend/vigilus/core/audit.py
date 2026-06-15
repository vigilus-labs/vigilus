"""Audit service – logs every action to the database with secret redaction."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.events import EventType, get_event_bus
from vigilus.db.models import Action, ActionOutcome

logger = structlog.get_logger(__name__)

# Keys whose values should be redacted
_SENSITIVE_PATTERN = re.compile(r"password|secret|key|token|passphrase", re.IGNORECASE)

# Maximum value length before redaction (2 KB)
_MAX_VALUE_LEN = 2048


def _redact_args(args: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a deep copy of *args* with sensitive values replaced."""
    if args is None:
        return None

    redacted = deepcopy(args)

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and _SENSITIVE_PATTERN.search(k):
                    obj[k] = "***REDACTED***"
                elif isinstance(v, str) and len(v) > _MAX_VALUE_LEN:
                    obj[k] = f"***TRUNCATED ({len(v)} bytes)***"
                else:
                    obj[k] = _walk(v)
        elif isinstance(obj, list):
            return [_walk(item) for item in obj]
        return obj

    _walk(redacted)
    return redacted


class AuditService:
    """Writes audit trail records to the ``actions`` table."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def log_action(
        self,
        *,
        event: str,
        actor: str,
        operator_id: str | None = None,
        tool_id: str | None = None,
        tool_name: str | None = None,
        server_id: str | None = None,
        args: dict[str, Any] | None = None,
        outcome: ActionOutcome | str = ActionOutcome.pending,
        error: str | None = None,
        duration_ms: float | None = None,
        session_id: str | None = None,
    ) -> Action:
        """Create an audit log entry and emit an event."""
        if isinstance(outcome, str):
            outcome = ActionOutcome(outcome)

        safe_args = _redact_args(args)

        action = Action(
            event=event,
            actor=actor,
            operator_id=operator_id,
            tool_id=tool_id,
            tool_name=tool_name,
            server_id=server_id,
            args=safe_args,
            outcome=outcome,
            error=error,
            duration_ms=duration_ms,
            session_id=session_id,
        )

        self._db.add(action)
        await self._db.flush()

        logger.info(
            "audit.action",
            action_id=action.id,
            event=event,
            actor=actor,
            outcome=outcome.value,
        )

        bus = get_event_bus()
        await bus.publish(EventType.ACTION_CREATED, {
            "action_id": action.id,
            "event": event,
            "actor": actor,
            "outcome": outcome.value,
        })

        return action

    async def update_action(
        self,
        action: Action,
        *,
        outcome: ActionOutcome | str | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> Action:
        """Update an existing action record and emit an update event."""
        if outcome is not None:
            if isinstance(outcome, str):
                outcome = ActionOutcome(outcome)
            action.outcome = outcome
        if error is not None:
            action.error = error
        if duration_ms is not None:
            action.duration_ms = duration_ms

        await self._db.flush()

        bus = get_event_bus()
        await bus.publish(EventType.ACTION_UPDATED, {
            "action_id": action.id,
            "event": action.event,
            "outcome": action.outcome.value if isinstance(action.outcome, ActionOutcome) else action.outcome,
        })

        return action
