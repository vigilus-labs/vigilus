import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from vigilus.db.models import Provider, Operator, Session, Message

@pytest.mark.asyncio
async def test_chat_crud(db_session: AsyncSession, async_client: AsyncClient):
    # 1. Create a Provider and Operator
    provider = Provider(name="Test Provider", type="openai_compat", base_url="http://localhost:8000")
    db_session.add(provider)
    await db_session.flush()
    op = Operator(
        name="Test Operator",
        description="A test operator",
        system_prompt="You are a test operator.",
        provider_id=provider.id,
        model="gpt-test"
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
