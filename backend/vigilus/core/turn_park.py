"""Park a turn that is waiting on JIT approval.

The checkpoint is committed before ``TurnParked`` is raised, so the waiting
coroutine can exit and a later approval can resume the same run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from uuid import uuid4

from vigilus.core.events import EventType, get_event_bus
from vigilus.db.models import Turn, TurnStatus
from vigilus.providers.base import LLMMessage


class TurnParked(Exception):
    """Raised after a turn has been checkpointed to wait for approval."""

    def __init__(self, turn_id: str):
        super().__init__(turn_id)
        self.turn_id = turn_id


@dataclass
class TurnParkContext:
    """Per-turn bag threaded from ``execute_turn`` down to tool execution."""

    session_id: str
    origin: str = "web"
    deliver_to: dict | None = None
    unattended: bool = False
    operator_messages: list = field(default_factory=list)
    operator_id: str | None = None
    turn_id: str | None = None
    # Orchestrator snapshot taken just before the delegation that may park.
    continuation: dict = field(default_factory=dict)


def dump_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """JSON-safe copy of an operator or orchestrator message list."""
    dumped: list[dict[str, Any]] = []
    for message in messages:
        dumped.append(
            {
                "role": message.role,
                "content": message.content,
                "tool_use_id": message.tool_use_id,
                "name": message.name,
                "tool_calls": message.tool_calls,
            }
        )
    return dumped


def load_messages(raw: list[dict[str, Any]] | None) -> list[LLMMessage]:
    """Rebuild ``LLMMessage`` rows from a checkpoint."""
    messages: list[LLMMessage] = []
    for item in raw or []:
        messages.append(
            LLMMessage(
                role=item.get("role") or "user",
                content=item.get("content") if item.get("content") is not None else "",
                tool_use_id=item.get("tool_use_id"),
                name=item.get("name"),
                tool_calls=item.get("tool_calls"),
            )
        )
    return messages


async def park_for_approval(
    db,
    ctx: TurnParkContext,
    req,
    tool,
    resource: str,
    permission,
    timeout: int,
    *,
    arguments: dict[str, Any] | None = None,
    tool_use_id: str | None = None,
    continuation: dict | None = None,
    operator_messages: list | None = None,
) -> NoReturn:
    """Write the awaiting-approval checkpoint and raise ``TurnParked``.

    Commits before raising. ``timeout`` is the wait window in seconds; past
    ``expires_at`` the sweeper resumes the turn as expired.
    """
    permission_value = getattr(permission, "value", str(permission))
    pending_call = {
        "tool": getattr(tool, "name", None) or str(tool),
        "arguments": arguments or {},
        "resource": resource,
        "permission": permission_value,
        "tool_use_id": tool_use_id,
        "continuation": continuation if continuation is not None else dict(ctx.continuation),
    }
    messages = (
        operator_messages if operator_messages is not None else list(ctx.operator_messages)
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=max(int(timeout), 0))

    turn = await db.get(Turn, ctx.turn_id) if ctx.turn_id else None
    if turn is None:
        turn = Turn(
            id=ctx.turn_id or str(uuid4()),
            session_id=ctx.session_id,
            origin=ctx.origin,
            deliver_to=ctx.deliver_to,
            unattended=ctx.unattended,
        )
        db.add(turn)

    turn.status = TurnStatus.awaiting_approval
    turn.origin = ctx.origin
    turn.operator_id = ctx.operator_id
    turn.pending_call = pending_call
    turn.operator_messages = messages
    turn.jit_request_id = req.id
    turn.deliver_to = ctx.deliver_to
    turn.unattended = ctx.unattended
    turn.expires_at = expires_at
    turn.error = None
    await db.commit()
    ctx.turn_id = turn.id

    await get_event_bus().publish(
        EventType.TURN_PARKED,
        {
            "id": turn.id,
            "session_id": ctx.session_id,
            "jit_request_id": req.id,
        },
    )
    raise TurnParked(turn.id)
