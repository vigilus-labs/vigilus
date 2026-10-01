"""Startup recovery and parked-turn expiry (#41)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vigilus.core.turn_recovery import expire_overdue_parks, recover_interrupted_turns
from vigilus.db.models import Session, Turn, TurnStatus


async def _session(db_session) -> Session:
    session = Session(title="recovery", origin="web")
    db_session.add(session)
    await db_session.commit()
    return session


async def test_startup_fails_running_turns_and_keeps_parked_ones(db_session):
    session = await _session(db_session)
    running = Turn(session_id=session.id, status=TurnStatus.running, origin="web")
    parked = Turn(
        session_id=session.id,
        status=TurnStatus.awaiting_approval,
        origin="web",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add_all([running, parked])
    await db_session.commit()

    assert await recover_interrupted_turns() == 1
    await db_session.refresh(running)
    await db_session.refresh(parked)
    assert running.status == TurnStatus.failed
    assert running.error == "Interrupted — backend restarted"
    assert parked.status == TurnStatus.awaiting_approval


async def test_overdue_park_resumes_as_expired(db_session, monkeypatch):
    session = await _session(db_session)
    overdue = Turn(
        session_id=session.id,
        status=TurnStatus.awaiting_approval,
        origin="web",
        expires_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    fresh = Turn(
        session_id=session.id,
        status=TurnStatus.awaiting_approval,
        origin="web",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add_all([overdue, fresh])
    await db_session.commit()

    seen: list[tuple[str, str]] = []

    async def _fake(turn_id: str, status: str) -> None:
        seen.append((turn_id, status))

    monkeypatch.setattr("vigilus.core.turn_resume.resume_turn", _fake)
    assert await expire_overdue_parks() == 1
    assert seen == [(overdue.id, "expired")]
    await db_session.refresh(fresh)
    assert fresh.status == TurnStatus.awaiting_approval
