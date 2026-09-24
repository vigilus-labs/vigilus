import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.db.models import Operator, Provider


@pytest.mark.asyncio
async def test_chat_crud(db_session: AsyncSession, async_client: AsyncClient):
    # 1. Create a Provider and Operator
    provider = Provider(
        name="Test Provider", type="openai_compat", base_url="http://localhost:8000"
    )
    db_session.add(provider)
    await db_session.flush()
    op = Operator(
        name="Test Operator",
        description="A test operator",
        system_prompt="You are a test operator.",
        provider_id=provider.id,
        model="gpt-test",
    )
    db_session.add(op)
    await db_session.commit()

    # 2. Create Session
    res = await async_client.post("/api/sessions", json={"operator_id": op.id})
    assert res.status_code == 200
    session_id = res.json()["id"]

    # 3. List Sessions
    res = await async_client.get("/api/sessions")
    assert res.status_code == 200
    assert len(res.json()) >= 1

    # 4. List Messages (Empty)
    res = await async_client.get(f"/api/sessions/{session_id}/messages")
    assert res.status_code == 200
    assert len(res.json()) == 0


async def test_send_message_rejects_concurrent_turn(db_session, async_client):
    """A session may only run one turn at a time — a second POST gets a 409."""
    from vigilus.core.tasks import get_task_registry

    provider = Provider(name="concurrent-prov", type="openai_compat", base_url="http://x")
    db_session.add(provider)
    await db_session.flush()
    op = Operator(
        name="Concurrent Op",
        description="d",
        system_prompt="p",
        provider_id=provider.id,
    )
    db_session.add(op)
    await db_session.commit()

    res = await async_client.post("/api/sessions", json={"operator_id": op.id})
    assert res.status_code == 200
    session_id = res.json()["id"]

    # Simulate a turn already in flight for this session.
    running = get_task_registry().register(session_id, "Already running")
    try:
        res = await async_client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello again"},
        )
        assert res.status_code == 409
        assert "already running" in res.json()["detail"].lower()
    finally:
        get_task_registry().unregister(session_id, running.id)

    # And once the turn is gone, the message goes through (it will fail at the
    # LLM stage since no provider is reachable, but it must not be a 409).
    res = await async_client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "hello again"},
    )
    assert res.status_code != 409
