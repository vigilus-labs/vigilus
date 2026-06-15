"""Tests for the inline JIT approval flow: deny → approve → retry succeeds."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from vigilus.db.models import (
    JitRequest,
    JitStatus,
    Operator,
    PermissionLevel,
    Tool,
    ToolImplementationType,
    TrustMode,
)
from vigilus.tools.registry import ToolRegistry


@pytest.fixture
async def denied_setup(db_session, tmp_path):
    """A read-level operator and a tool that requires exec (so calls are denied)."""
    op = Operator(
        name="JIT Flow Operator",
        description="test",
        permission_level=PermissionLevel.read,
        trust_mode=TrustMode.strict,
    )
    tool = Tool(
        name="jitflow_fs_list",
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


@pytest.mark.asyncio
async def test_denied_call_creates_pending_jit(denied_setup, db_session):
    op, tool, path = denied_setup
    registry = ToolRegistry()

    result = await registry.execute(tool.name, {"path": path}, operator=op, jit_wait_seconds=0)
    assert result.success is False
    assert "approval" in (result.error or "").lower()

    reqs = (await db_session.execute(
        select(JitRequest).where(JitRequest.operator_id == op.id)
    )).scalars().all()
    assert len(reqs) == 1
    assert reqs[0].status == JitStatus.pending


@pytest.mark.asyncio
async def test_retry_after_approval_succeeds(denied_setup, db_session):
    op, tool, path = denied_setup
    registry = ToolRegistry()

    # 1. First call is denied and creates a pending JIT request
    result = await registry.execute(tool.name, {"path": path}, operator=op, jit_wait_seconds=0)
    assert result.success is False

    req = (await db_session.execute(
        select(JitRequest).where(JitRequest.operator_id == op.id)
    )).scalar_one()

    # 2. User approves (inline in chat → same API the JIT page uses)
    from vigilus.core.rbac import WardenService

    token = await WardenService().approve_request(db_session, req.id, approver="test-user")
    assert token

    # 3. Retry of the exact same call now succeeds via the stored grant
    result = await registry.execute(tool.name, {"path": path}, operator=op, jit_wait_seconds=0)
    assert result.success is True, result.error


@pytest.mark.asyncio
async def test_retry_after_denial_still_denied(denied_setup, db_session):
    op, tool, path = denied_setup
    registry = ToolRegistry()

    await registry.execute(tool.name, {"path": path}, operator=op, jit_wait_seconds=0)
    req = (await db_session.execute(
        select(JitRequest).where(JitRequest.operator_id == op.id)
    )).scalar_one()

    from vigilus.core.rbac import WardenService

    await WardenService().deny_request(db_session, req.id, approver="test-user")

    result = await registry.execute(tool.name, {"path": path}, operator=op, jit_wait_seconds=0)
    assert result.success is False


@pytest.mark.asyncio
async def test_execution_pauses_until_approved(denied_setup, db_session):
    """The new blocking flow: the tool call waits, the user approves
    mid-wait, and the call proceeds without the LLM retrying."""
    import asyncio

    from vigilus.core.rbac import WardenService
    from vigilus.db.base import get_session_factory

    op, tool, path = denied_setup
    registry = ToolRegistry()
    factory = get_session_factory()

    async def approve_when_pending():
        for _ in range(60):
            await asyncio.sleep(0.05)
            async with factory() as adb:
                req = (await adb.execute(
                    select(JitRequest).where(
                        JitRequest.operator_id == op.id,
                        JitRequest.status == JitStatus.pending,
                    )
                )).scalars().first()
                if req:
                    await WardenService().approve_request(adb, req.id, approver="test-user")
                    return

    approver = asyncio.create_task(approve_when_pending())
    result = await registry.execute(
        tool.name, {"path": path}, operator=op, jit_wait_seconds=10
    )
    await approver

    assert result.success is True, result.error


@pytest.mark.asyncio
async def test_execution_pauses_until_denied(denied_setup, db_session):
    """Denying mid-wait aborts the call with a clear do-not-retry error."""
    import asyncio

    from vigilus.core.rbac import WardenService
    from vigilus.db.base import get_session_factory

    op, tool, path = denied_setup
    registry = ToolRegistry()
    factory = get_session_factory()

    async def deny_when_pending():
        for _ in range(60):
            await asyncio.sleep(0.05)
            async with factory() as adb:
                req = (await adb.execute(
                    select(JitRequest).where(
                        JitRequest.operator_id == op.id,
                        JitRequest.status == JitStatus.pending,
                    )
                )).scalars().first()
                if req:
                    await WardenService().deny_request(adb, req.id, approver="test-user")
                    return

    denier = asyncio.create_task(deny_when_pending())
    result = await registry.execute(
        tool.name, {"path": path}, operator=op, jit_wait_seconds=10
    )
    await denier

    assert result.success is False
    assert "DENIED" in (result.error or "")


@pytest.mark.asyncio
async def test_resolve_server_by_name_and_hostname(db_session):
    """ssh tools accept server name/hostname, not just UUID — and report
    missing credentials clearly."""
    from vigilus.core.crypto import encrypt
    from vigilus.db.models import Credential, CredentialType, Server, SshAuthMethod
    from vigilus.tools.native.ssh import _resolve_server, ssh_exec

    cred = Credential(
        name="arcane-login",
        type=CredentialType.password,
        ssh_auth_method=SshAuthMethod.password,
        username="creimer",
        secret=encrypt("hunter2"),
    )
    db_session.add(cred)
    await db_session.flush()
    srv = Server(name="Arcane", hostname="10.0.0.238", port=22, credential_id=cred.id)
    bare = Server(name="bare", hostname="10.0.0.99", port=22)
    db_session.add_all([srv, bare])
    await db_session.commit()

    # By exact id
    assert (await _resolve_server(db_session, srv.id))["hostname"] == "10.0.0.238"
    # By name, case-insensitive
    info = await _resolve_server(db_session, "arcane")
    assert info["hostname"] == "10.0.0.238"
    assert info["username"] == "creimer"
    assert info["secret"] == "hunter2"  # decrypted
    # By hostname
    assert (await _resolve_server(db_session, "10.0.0.238"))["server_name"] == "Arcane"
    # Unknown ref → helpful error listing inventory
    result = await ssh_exec({"server_id": "user@10.0.0.238", "command": "uptime"}, db=db_session)
    assert "Available servers" in result["error"]
    assert "Arcane" in result["error"]
    # Server without credential → clear guidance
    result = await ssh_exec({"server_id": "bare", "command": "uptime"}, db=db_session)
    assert "no usable credential" in result["error"]
