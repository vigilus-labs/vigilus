import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.db.models import JitStatus, Operator, Provider, TrustMode


@pytest.mark.asyncio
async def test_jit_api(db_session: AsyncSession, async_client: AsyncClient):
    # Setup Provider and Operator
    provider = Provider(name="Test Provider", type="openai_compat")
    db_session.add(provider)
    await db_session.flush()
    op = Operator(
        name="Test Operator",
        description="A test operator",
        provider_id=provider.id,
        trust_mode=TrustMode.strict,
    )
    db_session.add(op)
    await db_session.commit()

    # 1. Create a JIT request via WardenService
    from vigilus.core.rbac import Permission, WardenService

    req, token = await WardenService().request_jit(
        db_session, op, "/etc/passwd", Permission.write, "Need to test"
    )
    assert req.status == JitStatus.pending
    assert token is None
    req_id = req.id

    # 2. List JIT Requests via API
    res = await async_client.get("/api/jit")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending"

    # 3. Approve JIT via API
    res = await async_client.post(f"/api/jit/{req_id}/approve")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "approved"
    assert data["token_id"] is not None

    # 4. Deny JIT via API
    # Create another one
    req2, _ = await WardenService().request_jit(
        db_session, op, "/etc/shadow", Permission.exec, "Need to test 2"
    )
    res = await async_client.post(f"/api/jit/{req2.id}/deny")
    assert res.status_code == 200
    assert res.json()["status"] == "denied"
