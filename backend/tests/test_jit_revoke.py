"""Revoking approved JIT grants before their TTL expires (#34)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.rbac import Permission, WardenService
from vigilus.db.models import JitStatus, Operator, Provider, TrustMode


@pytest_asyncio.fixture
async def strict_operator(db_session: AsyncSession):
    provider = Provider(name="Test Provider", type="openai_compat")
    db_session.add(provider)
    await db_session.flush()
    op = Operator(
        name="Test Operator",
        description="operator",
        provider_id=provider.id,
        trust_mode=TrustMode.strict,
    )
    db_session.add(op)
    await db_session.commit()
    return op


async def _approved_request(db_session: AsyncSession, op: Operator):
    warden = WardenService()
    req, _ = await warden.request_jit(
        db_session, op, "/etc/nginx", Permission.write, "need to reload"
    )
    await warden.approve_request(db_session, req.id, approver="admin")
    await db_session.refresh(req)
    return req


@pytest.mark.asyncio
async def test_revoke_approved_request(db_session: AsyncSession, strict_operator):
    req = await _approved_request(db_session, strict_operator)
    assert req.status == JitStatus.approved

    warden = WardenService()
    await warden.revoke_grant(db_session, req.id, approver="admin_ui")

    await db_session.refresh(req)
    assert req.status == JitStatus.revoked
    assert req.approved_by == "admin_ui"
    assert req.resolved_at is not None


@pytest.mark.asyncio
async def test_revoke_pending_request_raises(db_session: AsyncSession, strict_operator):
    warden = WardenService()
    req, _ = await warden.request_jit(
        db_session, strict_operator, "/etc/nginx", Permission.write, "pending"
    )
    assert req.status == JitStatus.pending

    with pytest.raises(ValueError):
        await warden.revoke_grant(db_session, req.id, approver="admin_ui")
    await db_session.refresh(req)
    assert req.status == JitStatus.pending


@pytest.mark.asyncio
async def test_revoke_denied_request_raises(db_session: AsyncSession, strict_operator):
    warden = WardenService()
    req, _ = await warden.request_jit(
        db_session, strict_operator, "/etc/nginx", Permission.write, "denied"
    )
    await warden.deny_request(db_session, req.id, approver="admin")

    with pytest.raises(ValueError):
        await warden.revoke_grant(db_session, req.id, approver="admin_ui")
    await db_session.refresh(req)
    assert req.status == JitStatus.denied


@pytest.mark.asyncio
async def test_revoke_already_revoked_request_raises(
    db_session: AsyncSession, strict_operator
):
    req = await _approved_request(db_session, strict_operator)
    warden = WardenService()
    await warden.revoke_grant(db_session, req.id, approver="admin_ui")

    with pytest.raises(ValueError):
        await warden.revoke_grant(db_session, req.id, approver="admin_ui")


@pytest.mark.asyncio
async def test_revoke_publishes_jit_resolved_with_revoked_status(
    db_session: AsyncSession, strict_operator
):
    from vigilus.core.events import get_event_bus

    req = await _approved_request(db_session, strict_operator)

    events: list[dict] = []

    async def _record(payload: dict) -> None:
        events.append(payload)

    bus = get_event_bus()
    bus.subscribe("jit.resolved", _record)
    try:
        warden = WardenService()
        await warden.revoke_grant(db_session, req.id, approver="admin_ui")
    finally:
        bus.unsubscribe("jit.resolved", _record)

    resolved = [e for e in events if e.get("status") == "revoked"]
    assert resolved, f"expected a revoked jit.resolved event, got {events}"
    assert resolved[0]["id"] == req.id
    assert resolved[0]["operator_id"] == strict_operator.id
