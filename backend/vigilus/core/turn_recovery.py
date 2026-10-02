"""Startup recovery and expiry for parked turns.

A process restart fails turns that were ``running`` (their coroutine is gone)
and leaves ``awaiting_approval`` rows in place so a later approval can resume
them. The sweeper resumes a parked turn whose wait window has passed, as a
denial, so the operator reports the timeout instead of waiting forever.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from vigilus.db.models import Turn, TurnStatus

logger = structlog.get_logger(__name__)

_INTERRUPTED = "Interrupted — backend restarted"


async def recover_interrupted_turns() -> int:
    """Mark in-flight turns failed. Parked turns stay awaiting approval.

    Returns the number of rows marked failed.
    """
    from vigilus.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            (await db.execute(select(Turn).where(Turn.status == TurnStatus.running))).scalars().all()
        )
        for turn in rows:
            turn.status = TurnStatus.failed
            turn.error = _INTERRUPTED
        if rows:
            logger.info("turn.startup_interrupted", count=len(rows))
        await db.commit()
        return len(rows)


async def expire_overdue_parks() -> int:
    """Resume parked turns whose approval window has passed.

    Returns how many resumes were started.
    """
    from vigilus.core.turn_resume import resume_turn

    now = datetime.now(UTC)
    from vigilus.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(Turn.id).where(
                        Turn.status == TurnStatus.awaiting_approval,
                        Turn.expires_at.is_not(None),
                        Turn.expires_at < now,
                    )
                )
            )
            .scalars()
            .all()
        )
        turn_ids = list(rows)
    for turn_id in turn_ids:
        await resume_turn(turn_id, "expired")
    if turn_ids:
        logger.info("turn.expired", count=len(turn_ids))
    return len(turn_ids)


async def run_park_sweeper(stop: asyncio.Event, *, interval: float = 60) -> None:
    """Periodically expire parked turns until ``stop`` is set."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            try:
                await expire_overdue_parks()
            except Exception:
                logger.exception("turn.sweeper_failed")
