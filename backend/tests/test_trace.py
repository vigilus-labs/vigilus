"""Per-reply work trace: grouping audit rows by turn, and the trace endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from vigilus.core.trace import MAX_STEPS, group_actions_by_reply

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _msg(id: str, role: str, t: int, content="text"):
    return SimpleNamespace(id=id, role=role, created_at=_at(t), content=content)


def _act(id: str, t: int, event: str = "tool_call_end"):
    return SimpleNamespace(id=id, event=event, created_at=_at(t))


def _ids(trace) -> list[str]:
    return [a.id for a in trace.steps]


# ── Grouping ──────────────────────────────────────────────────────────────────


def test_group_assigns_actions_to_reply_in_window():
    messages = [
        _msg("u1", "user", 0),
        _msg("a1", "assistant", 5),
        _msg("u2", "user", 10),
        _msg("a2", "assistant", 15),
    ]
    actions = [_act("x1", 2), _act("x2", 12)]
    traces = group_actions_by_reply(messages, actions)
    assert _ids(traces["a1"]) == ["x1"]
    assert _ids(traces["a2"]) == ["x2"]


def test_group_parked_then_resumed_attaches_to_final_reply():
    messages = [
        _msg("u1", "user", 0),
        _msg("park", "assistant", 3, "This turn is awaiting approval."),
        _msg("d1", "tool", 8, {"operator": "infra", "result": "ok"}),
        _msg("final", "assistant", 9),
    ]
    actions = [_act("x1", 1, "tool_denied"), _act("x2", 6)]
    traces = group_actions_by_reply(messages, actions)
    assert set(traces) == {"final"}
    assert _ids(traces["final"]) == ["x1", "x2"]


def test_group_skips_non_text_assistant_rows():
    messages = [
        _msg("u1", "user", 0),
        _msg("a1", "assistant", 4),
        _msg("a2", "assistant", 5, {"delegation": True}),
        _msg("a3", "assistant", 6, "   "),
    ]
    traces = group_actions_by_reply(messages, [_act("x1", 2)])
    assert set(traces) == {"a1"}


def test_group_ignores_tool_call_start():
    messages = [_msg("u1", "user", 0), _msg("a1", "assistant", 9)]
    actions = [_act("s1", 1, "tool_call_start"), _act("e1", 2), _act("j1", 3, "jit_requested")]
    traces = group_actions_by_reply(messages, actions)
    assert _ids(traces["a1"]) == ["e1"]


def test_group_turn_without_assistant_reply_is_dropped():
    messages = [
        _msg("u1", "user", 0),  # failed/cancelled turn: no reply
        _msg("u2", "user", 10),
        _msg("a2", "assistant", 15),
    ]
    actions = [_act("x1", 2), _act("x2", 12)]
    traces = group_actions_by_reply(messages, actions)
    assert set(traces) == {"a2"}
    assert _ids(traces["a2"]) == ["x2"]


def test_group_ignores_actions_before_first_user_message():
    messages = [_msg("u1", "user", 10), _msg("a1", "assistant", 15)]
    traces = group_actions_by_reply(messages, [_act("x0", 1), _act("x1", 12)])
    assert _ids(traces["a1"]) == ["x1"]


def test_group_caps_steps():
    messages = [_msg("u1", "user", 0), _msg("a1", "assistant", 10_000)]
    actions = [_act(f"x{i}", 1 + i) for i in range(MAX_STEPS + 7)]
    trace = group_actions_by_reply(messages, actions)["a1"]
    assert len(trace.steps) == MAX_STEPS
    assert trace.truncated == 7
    assert trace.steps[0].id == "x0"


def test_group_handles_naive_and_aware_timestamps():
    # SQLite hands back naive datetimes; both sides must compare cleanly.
    messages = [
        SimpleNamespace(id="u1", role="user", created_at=_at(0).replace(tzinfo=None), content="q"),
        _msg("a1", "assistant", 5),
    ]
    actions = [SimpleNamespace(id="x1", event="tool_error", created_at=_at(2).replace(tzinfo=None))]
    assert _ids(group_actions_by_reply(messages, actions)["a1"]) == ["x1"]


# ── Endpoint ──────────────────────────────────────────────────────────────────


async def _seed_turn(db):
    from vigilus.db.models import (
        Action,
        ActionOutcome,
        Message,
        MessageRole,
        Operator,
        Server,
        Session,
    )

    session = Session(id="s1", title="t")
    op = Operator(id="op1", name="infra-ops", description="d")
    srv = Server(id="srv1", name="pve-02", hostname="10.0.0.2")
    db.add_all([session, op, srv])
    db.add(
        Message(session_id="s1", role=MessageRole.user, content="restart nginx", created_at=_at(0))
    )
    db.add_all(
        [
            Action(
                id="act-start",
                event="tool_call_start",
                actor="infra-ops",
                operator_id="op1",
                tool_name="ssh_exec",
                args={"server_id": "srv1", "command": "systemctl restart nginx"},
                outcome=ActionOutcome.pending,
                session_id="s1",
                created_at=_at(1),
            ),
            Action(
                id="act-end",
                event="tool_call_end",
                actor="infra-ops",
                operator_id="op1",
                tool_name="ssh_exec",
                # Historic row written before write-path redaction.
                args={
                    "server_id": "srv1",
                    "command": "systemctl restart nginx",
                    "password": "hunter2",
                },
                outcome=ActionOutcome.success,
                output="big output that must not ride along",
                duration_ms=1234.5,
                session_id="s1",
                created_at=_at(2),
            ),
            Action(
                id="act-other-session",
                event="tool_call_end",
                actor="infra-ops",
                tool_name="ssh_exec",
                outcome=ActionOutcome.success,
                session_id="elsewhere",
                created_at=_at(2),
            ),
        ]
    )
    reply = Message(session_id="s1", role=MessageRole.assistant, content="Done.", created_at=_at(3))
    db.add(reply)
    await db.commit()
    return reply


@pytest.mark.asyncio
async def test_trace_endpoint_shape(async_client, db_session):
    reply = await _seed_turn(db_session)
    r = await async_client.get("/api/sessions/s1/trace")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    turn = body[0]
    assert turn["message_id"] == reply.id
    assert turn["truncated"] == 0
    [step] = turn["steps"]
    assert step["action_id"] == "act-end"
    assert step["tool_name"] == "ssh_exec"
    assert step["actor"] == "infra-ops"
    assert step["outcome"] == "success"
    assert step["duration_ms"] == 1234.5
    assert step["server_name"] == "pve-02"
    assert "output" not in step


@pytest.mark.asyncio
async def test_trace_redacts_args(async_client, db_session):
    await _seed_turn(db_session)
    r = await async_client.get("/api/sessions/s1/trace")
    step = r.json()[0]["steps"][0]
    assert step["args"]["password"] == "***REDACTED***"
    assert step["args"]["command"] == "systemctl restart nginx"


@pytest.mark.asyncio
async def test_trace_server_name_falls_back_to_reference(async_client, db_session):
    from vigilus.db.models import Action, ActionOutcome, Message, MessageRole, Session

    db_session.add(Session(id="s2"))
    db_session.add(Message(session_id="s2", role=MessageRole.user, content="q", created_at=_at(0)))
    db_session.add(
        Action(
            event="tool_call_end",
            actor="op",
            tool_name="docker_ps",
            args={"server_id": "arcane"},  # LLMs often pass a name, not a UUID
            outcome=ActionOutcome.success,
            session_id="s2",
            created_at=_at(1),
        )
    )
    db_session.add(
        Message(session_id="s2", role=MessageRole.assistant, content="ok", created_at=_at(2))
    )
    await db_session.commit()
    step = (await async_client.get("/api/sessions/s2/trace")).json()[0]["steps"][0]
    assert step["server_name"] == "arcane"


@pytest.mark.asyncio
async def test_trace_unknown_session_404(async_client):
    r = await async_client.get("/api/sessions/nope/trace")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_trace_requires_auth(unauthenticated_client):
    r = await unauthenticated_client.get("/api/sessions/s1/trace")
    assert r.status_code == 401
