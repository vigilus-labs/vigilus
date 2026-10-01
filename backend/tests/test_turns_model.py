"""Turn checkpoint model (#41)."""

from __future__ import annotations

from datetime import UTC, datetime

from vigilus.db.models import Session, Turn, TurnStatus


async def test_turn_round_trip(db_session):
    session = Session(title="checkpoint", origin="web")
    db_session.add(session)
    await db_session.commit()

    turn = Turn(
        session_id=session.id,
        status=TurnStatus.awaiting_approval,
        origin="schedule",
        pending_call={"tool": "fs_write", "arguments": {"path": "/etc/nginx"}},
        operator_messages=[{"role": "user", "content": "do it"}],
        jit_request_id="jit-1",
        deliver_to={"platform": "telegram", "chat_id": "42"},
        unattended=True,
        expires_at=datetime.now(UTC),
    )
    db_session.add(turn)
    await db_session.commit()

    loaded = await db_session.get(Turn, turn.id)
    assert loaded is not None
    assert loaded.status == TurnStatus.awaiting_approval
    assert loaded.pending_call["tool"] == "fs_write"
    assert loaded.operator_messages[0]["role"] == "user"
    assert loaded.unattended is True
    assert loaded.deliver_to["platform"] == "telegram"
    assert {item.value for item in TurnStatus} == {
        "running",
        "awaiting_approval",
        "completed",
        "failed",
        "cancelled",
    }
