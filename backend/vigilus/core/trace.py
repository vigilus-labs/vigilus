"""Per-reply work trace: which audited tool calls produced each assistant reply.

The audit log (``actions``) already records every tool call with its session,
operator, args, outcome and duration. A session runs one turn at a time (a
second message gets a 409), so an action belongs to the turn whose window it
falls in: from one user message up to the next. The trace is attached to the
last text reply in that window, which also covers a turn that parked on
approval and resumed later. No turn id is stored on actions, so grouping is by
time alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# One row per finished call: start rows are paired with these, so keeping
# them too would double every step.
TRACE_EVENTS = ("tool_call_end", "tool_error", "tool_denied")

# A runaway turn can log hundreds of calls; past this the trace says how many
# were left out rather than shipping them all with every session load.
MAX_STEPS = 200


@dataclass
class TurnTrace:
    message_id: str
    steps: list[Any] = field(default_factory=list)
    truncated: int = 0


def _utc(ts: datetime) -> datetime:
    # SQLite returns naive datetimes even for timezone=True columns.
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def _role(msg: Any) -> str:
    return getattr(msg.role, "value", msg.role)


def _is_text_reply(msg: Any) -> bool:
    return _role(msg) == "assistant" and isinstance(msg.content, str) and bool(msg.content.strip())


def group_actions_by_reply(messages: Sequence[Any], actions: Sequence[Any]) -> dict[str, TurnTrace]:
    """Map assistant message id -> the trace of the turn that produced it.

    *messages* and *actions* are rows (or anything with the same attributes)
    for one session. Turns with no text reply (failed or cancelled) are left
    out rather than leaking into a neighbouring turn.
    """
    ordered = sorted(messages, key=lambda m: _utc(m.created_at))

    # (window start, reply id) per turn, in order.
    windows: list[tuple[datetime, str | None]] = []
    for msg in ordered:
        if _role(msg) == "user":
            windows.append((_utc(msg.created_at), None))
        elif windows and _is_text_reply(msg):
            windows[-1] = (windows[-1][0], msg.id)

    traces: dict[str, TurnTrace] = {}
    if not windows:
        return traces

    idx = 0
    for action in sorted(
        (a for a in actions if a.event in TRACE_EVENTS), key=lambda a: _utc(a.created_at)
    ):
        ts = _utc(action.created_at)
        if ts < windows[0][0]:
            continue
        while idx + 1 < len(windows) and ts >= windows[idx + 1][0]:
            idx += 1
        reply_id = windows[idx][1]
        if reply_id is None:
            continue
        trace = traces.setdefault(reply_id, TurnTrace(message_id=reply_id))
        if len(trace.steps) < MAX_STEPS:
            trace.steps.append(action)
        else:
            trace.truncated += 1
    return traces
