"""Revoking approved JIT grants before their TTL expires (#34)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.rbac import Permission, WardenService
from vigilus.db.models import (
    JitStatus,
    Operator,
    PermissionLevel,
    Provider,
    Tool,
    ToolImplementationType,
    TrustMode,
)


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


# ── Task 1.2: DB-backed revocation check at enforcement points ──────────


@pytest.fixture
async def revocation_setup(db_session, tmp_path):
    """A read-level strict operator and an exec-gated native tool."""
    op = Operator(
        name="Revocation Operator",
        description="test",
        permission_level=PermissionLevel.read,
        trust_mode=TrustMode.strict,
    )
    tool = Tool(
        name="revocation_fs_list",
        description="fs_list gated behind exec for testing",
        implementation_type=ToolImplementationType.native,
        required_permission=PermissionLevel.exec,
        native_handler="fs_list",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    db_session.add_all([op, tool])
    await db_session.commit()
    await db_session.refresh(op)
    return op, tool, str(tmp_path)


async def _grant_for(db_session, op, resource, permission=Permission.exec):
    from vigilus.core.rbac import Permission as _Permission

    warden = WardenService()
    req, _ = await warden.request_jit(
        db_session, op, resource, _Permission(permission), "test grant"
    )
    token = await warden.approve_request(db_session, req.id, approver="test-user")
    return req, token


@pytest.mark.asyncio
async def test_revoked_token_rejected_via_jit_token_args(revocation_setup, db_session):
    """A revoked grant's raw token must not authorize a call when the LLM
    presents it via jit_token args — tokens are stateless, the DB is truth."""
    from vigilus.tools.registry import ToolRegistry

    op, tool, path = revocation_setup
    req, token = await _grant_for(db_session, op, path)
    assert token

    warden = WardenService()
    await warden.revoke_grant(db_session, req.id, approver="test-user")

    registry = ToolRegistry()
    result = await registry.execute(
        tool.name, {"path": path, "jit_token": token}, operator=op, jit_wait_seconds=0
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_active_token_authorizes_call(revocation_setup, db_session):
    """The happy path is unchanged: a live grant's token authorizes the call."""
    from vigilus.tools.registry import ToolRegistry

    op, tool, path = revocation_setup
    _req, token = await _grant_for(db_session, op, path)
    assert token

    registry = ToolRegistry()
    result = await registry.execute(
        tool.name, {"path": path, "jit_token": token}, operator=op, jit_wait_seconds=0
    )
    assert result.success is True, result.error


@pytest.mark.asyncio
async def test_is_token_active_fails_closed_on_db_error(strict_operator):
    """If the revocation lookup itself errors, the token is treated as
    inactive — an outage must never widen access."""

    class _BrokenDB:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("db down")

    warden = WardenService()
    token = warden.issue_token(strict_operator.id, "/etc/nginx", Permission.write, 15)

    assert await warden.is_token_active(_BrokenDB(), token) is None


@pytest.mark.asyncio
async def test_reuse_lookup_excludes_revoked_grant(revocation_setup, db_session):
    """After a revoke, the stored-grant reuse path must not re-authorize
    the same call (grant cache is cleared via jit.resolved)."""
    from vigilus.tools.registry import ToolRegistry

    op, tool, path = revocation_setup
    req, _token = await _grant_for(db_session, op, path)

    registry = ToolRegistry()
    first = await registry.execute(tool.name, {"path": path}, operator=op, jit_wait_seconds=0)
    assert first.success is True, first.error

    await WardenService().revoke_grant(db_session, req.id, approver="test-user")

    second = await registry.execute(tool.name, {"path": path}, operator=op, jit_wait_seconds=0)
    assert second.success is False
