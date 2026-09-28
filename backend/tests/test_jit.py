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


# ── #34: POST /api/jit/{id}/revoke ──────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_endpoint_revokes_active_grant(
    db_session: AsyncSession, async_client: AsyncClient
):
    from vigilus.core.events import get_event_bus
    from vigilus.core.rbac import Permission, WardenService

    provider = Provider(name="Test Provider", type="openai_compat")
    db_session.add(provider)
    await db_session.flush()
    op = Operator(
        name="Revoke Op",
        description="test",
        provider_id=provider.id,
        trust_mode=TrustMode.strict,
    )
    db_session.add(op)
    await db_session.commit()

    warden = WardenService()
    req, _ = await warden.request_jit(
        db_session, op, "/etc/nginx", Permission.write, "reload nginx"
    )
    token = await warden.approve_request(db_session, req.id, approver="admin")
    assert token

    events: list[dict] = []

    async def _record(payload: dict) -> None:
        events.append(payload)

    bus = get_event_bus()
    bus.subscribe("jit.resolved", _record)
    try:
        res = await async_client.post(
            f"/api/jit/{req.id}/revoke", json={"approved_by": "admin_ui"}
        )
    finally:
        bus.unsubscribe("jit.resolved", _record)

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "revoked"
    assert data["approved_by"] == "admin_ui"
    assert any(e.get("status") == "revoked" and e.get("id") == req.id for e in events)

    await db_session.refresh(req)
    assert req.status == JitStatus.revoked


@pytest.mark.asyncio
async def test_revoke_endpoint_rejects_pending_request(
    db_session: AsyncSession, async_client: AsyncClient
):
    from vigilus.core.rbac import Permission, WardenService

    provider = Provider(name="Test Provider", type="openai_compat")
    db_session.add(provider)
    await db_session.flush()
    op = Operator(
        name="Revoke Op 2",
        description="test",
        provider_id=provider.id,
        trust_mode=TrustMode.strict,
    )
    db_session.add(op)
    await db_session.commit()

    req, _ = await WardenService().request_jit(
        db_session, op, "/etc/nginx", Permission.write, "reload nginx"
    )

    res = await async_client.post(f"/api/jit/{req.id}/revoke")
    assert res.status_code == 400
    await db_session.refresh(req)
    assert req.status == JitStatus.pending


@pytest.mark.asyncio
async def test_revoke_endpoint_unknown_request_404(async_client: AsyncClient):
    res = await async_client.post("/api/jit/nonexistent-id/revoke")
    assert res.status_code == 404
