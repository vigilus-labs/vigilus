"""Prompt memory selection keeps the newest rows, in chronological order."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vigilus.core.memory import MAX_PROMPT_MEMORIES, get_memories
from vigilus.db.models import Memory


def _memory(scope: str, content: str, when: datetime, *, memory_id: str | None = None) -> Memory:
    memory = Memory(scope=scope, content=content, created_at=when)
    if memory_id is not None:
        memory.id = memory_id
    return memory


@pytest.mark.asyncio
async def test_get_memories_keeps_newest_and_returns_oldest_first(db_session):
    """With more than the cap, the oldest row is dropped and the window is chronological."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    total = MAX_PROMPT_MEMORIES + 1
    for i in range(total):
        db_session.add(_memory("global", f"fact-{i}", base + timedelta(minutes=i)))
    await db_session.commit()

    memories = await get_memories(db_session, ["global"])

    contents = [m.content for m in memories]
    assert len(contents) == MAX_PROMPT_MEMORIES
    assert "fact-0" not in contents
    assert contents[0] == "fact-1"
    assert contents[-1] == f"fact-{total - 1}"
    assert contents == sorted(contents, key=lambda c: int(c.split("-")[1]))


@pytest.mark.asyncio
async def test_get_memories_ignores_other_scopes(db_session):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.add(_memory("global", "shared", base))
    db_session.add(_memory("orchestrator", "private", base + timedelta(minutes=1)))
    db_session.add(_memory("other-operator", "hidden", base + timedelta(minutes=2)))
    await db_session.commit()

    memories = await get_memories(db_session, ["global", "orchestrator"])

    assert [m.content for m in memories] == ["shared", "private"]


@pytest.mark.asyncio
async def test_get_memories_returns_all_when_under_the_cap(db_session):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.add(_memory("global", "older", base))
    db_session.add(_memory("global", "newer", base + timedelta(minutes=1)))
    await db_session.commit()

    memories = await get_memories(db_session, ["global"])

    assert [m.content for m in memories] == ["older", "newer"]


@pytest.mark.asyncio
async def test_get_memories_tie_breaks_on_id(db_session):
    when = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.add(_memory("global", "zzz-id", when, memory_id="zzz"))
    db_session.add(_memory("global", "aaa-id", when, memory_id="aaa"))
    await db_session.commit()

    memories = await get_memories(db_session, ["global"])

    assert [m.content for m in memories] == ["aaa-id", "zzz-id"]
