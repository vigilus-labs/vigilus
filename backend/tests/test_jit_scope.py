"""Tests for JIT approval granularity: once vs timed, custom/clamped TTL,
resource scoping, and single-use exclusion from token reuse."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.rbac import Permission, WardenService
from vigilus.db.models import (
    JitRequest,
    JitStatus,
    Operator,
    PermissionLevel,
    Provider,
    TrustMode,
)
from vigilus.tools.registry import ToolRegistry


async def _make_operator(db: AsyncSession) -> Operator:
    provider = Provider(name="P", type="openai_compat")
    db.add(provider)
    await db.flush()
    op = Operator(
        name="ScopeOp",
        description="t",
        provider_id=provider.id,
        permission_level=PermissionLevel.write,
        trust_mode=TrustMode.strict,
    )
    db.add(op)
    await db.commit()
    return op


def test_extract_resource():
    extract = ToolRegistry._extract_resource
    assert extract({"server_id": "web01", "command": "uptime"}) == "server:web01"
    assert extract({"path": "/etc/nginx/nginx.conf"}) == "/etc/nginx/nginx.conf"
    assert extract({"resource": "custom"}) == "custom"
    assert extract({"command": "uptime"}) == "*"


@pytest.mark.asyncio
async def test_approve_single_use(db_session: AsyncSession, async_client: AsyncClient):
    op = await _make_operator(db_session)
    req, token = await WardenService().request_jit(
        db_session, op, "server:web01", Permission.exec, "patch"
    )
    assert token is None  # strict → pending

    res = await async_client.post(
        f"/api/jit/{req.id}/approve", json={"single_use": True}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "approved"
    assert body["scope_mode"] == "once"


@pytest.mark.asyncio
async def test_approve_custom_ttl_clamped(db_session: AsyncSession, async_client: AsyncClient):
    op = await _make_operator(db_session)
    req, _ = await WardenService().request_jit(
        db_session, op, "server:web01", Permission.exec, "patch"
    )
    # Ask for an absurd TTL → clamped to jit_max_ttl_minutes.
    from vigilus.config import get_settings

    res = await async_client.post(
        f"/api/jit/{req.id}/approve", json={"ttl_minutes": 99999}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["scope_mode"] == "timed"
    assert body["ttl_minutes"] == get_settings().jit_max_ttl_minutes


@pytest.mark.asyncio
async def test_approve_broaden_resource(db_session: AsyncSession, async_client: AsyncClient):
    op = await _make_operator(db_session)
    req, _ = await WardenService().request_jit(
        db_session, op, "server:web01", Permission.exec, "patch"
    )
    res = await async_client.post(
        f"/api/jit/{req.id}/approve", json={"resource": "*"}
    )
    assert res.status_code == 200
    assert res.json()["resource"] == "*"


@pytest.mark.asyncio
async def test_find_approved_token_skips_once(db_session: AsyncSession):
    op = await _make_operator(db_session)
    warden = WardenService()
    reg = ToolRegistry()

    # A "once" grant must not be reused — _find_approved_token returns None.
    once_token = warden.issue_token(op.id, "*", Permission.exec, 15)
    db_session.add(
        JitRequest(
            operator_id=op.id,
            resource="*",
            permission="exec",
            task_description="once",
            status=JitStatus.approved,
            token_id=once_token,
            ttl_minutes=15,
            scope_mode="once",
        )
    )
    await db_session.commit()
    found = await reg._find_approved_token(db_session, op, "server:web01", Permission.exec)
    assert found is None

    # A "timed" grant is reusable.
    timed_token = warden.issue_token(op.id, "*", Permission.exec, 15)
    db_session.add(
        JitRequest(
            operator_id=op.id,
            resource="*",
            permission="exec",
            task_description="timed",
            status=JitStatus.approved,
            token_id=timed_token,
            ttl_minutes=15,
            scope_mode="timed",
        )
    )
    await db_session.commit()
    found = await reg._find_approved_token(db_session, op, "server:web01", Permission.exec)
    assert found is not None
    assert found.permission >= Permission.exec
