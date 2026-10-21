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


@pytest.mark.asyncio
async def test_delete_session_with_turns_and_actions(db_session, async_client):
    """Deleting a session removes its messages and turns and keeps the audit rows.

    Turns are NOT NULL on session_id, so the ORM must delete them rather than
    try to null the FK. Actions are audit history and survive with a NULL
    session_id.
    """
    from sqlalchemy import select

    from vigilus.db.models import Action, Message, MessageRole, Session, Turn, TurnStatus

    session = Session(title="to delete")
    db_session.add(session)
    await db_session.flush()
    db_session.add(Message(session_id=session.id, role=MessageRole.user, content="hi"))
    db_session.add(Turn(session_id=session.id, status=TurnStatus.completed))
    action = Action(event="tool.call", actor="test", session_id=session.id)
    db_session.add(action)
    await db_session.commit()
    session_id, action_id = session.id, action.id

    res = await async_client.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 200, res.text

    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None
    turns = (await db_session.execute(select(Turn).where(Turn.session_id == session_id))).all()
    assert turns == []
    kept = await db_session.get(Action, action_id)
    assert kept is not None and kept.session_id is None
