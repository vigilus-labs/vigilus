import pytest

from vigilus.core.rbac import Permission, PolicyEngine, WardenService
from vigilus.db.models import Operator, PermissionLevel, Tool
from vigilus.tools.registry import ToolRegistry


@pytest.fixture
def policy_engine():
    return PolicyEngine()


@pytest.fixture
def warden():
    return WardenService()


def test_warden_service(warden):
    token = warden.issue_token(
        operator_id="op123", resource="/etc/nginx", permission=Permission.write, ttl_minutes=15
    )
    assert token is not None

    jit = warden.validate_token(token)
    assert jit is not None
    assert jit.operator_id == "op123"
    assert jit.resource == "/etc/nginx"
    assert jit.permission == Permission.write


@pytest.mark.asyncio
async def test_policy_engine_allow_high_base_permission(policy_engine):
    op = Operator(id="op1", permission_level=PermissionLevel.elevate, working_dir=None)
    allowed = await policy_engine.check_permission(
        operator=op,
        required_permission=Permission.write,
        resource_path="/etc/shadow",
        tool_name="test_tool",
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_policy_engine_deny_low_base_permission(policy_engine):
    op = Operator(id="op1", permission_level=PermissionLevel.read, working_dir=None)
    allowed = await policy_engine.check_permission(
        operator=op,
        required_permission=Permission.write,
        resource_path="/etc/shadow",
        tool_name="test_tool",
    )
    assert allowed is False


@pytest.mark.asyncio
async def test_policy_engine_allow_with_jit(policy_engine, warden):
    op = Operator(id="op1", permission_level=PermissionLevel.read, working_dir=None)
    token_str = warden.issue_token("op1", "/etc/nginx", Permission.write)
    token = warden.validate_token(token_str)

    allowed = await policy_engine.check_permission(
        operator=op,
        required_permission=Permission.write,
        resource_path="/etc/nginx/nginx.conf",
        jit_token=token,
        tool_name="test_tool",
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_policy_engine_deny_outside_working_dir(policy_engine):
    op = Operator(id="op1", permission_level=PermissionLevel.write, working_dir="/app")
    allowed = await policy_engine.check_permission(
        operator=op,
        required_permission=Permission.write,
        resource_path="/etc/shadow",
        tool_name="test_tool",
    )
    assert allowed is False


@pytest.mark.asyncio
async def test_policy_engine_allow_inside_working_dir(policy_engine):
    op = Operator(id="op1", permission_level=PermissionLevel.write, working_dir="/app")
    allowed = await policy_engine.check_permission(
        operator=op,
        required_permission=Permission.write,
        resource_path="/app/config.json",
        tool_name="test_tool",
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_tool_registry_resolve_missing():
    registry = ToolRegistry()
    op = Operator(id="op1", name="op1", description="test", permission_level=PermissionLevel.read)
    res = await registry.execute("missing", {}, operator=op)
    assert res.success is False
    assert "not found" in res.error


@pytest.mark.asyncio
async def test_tool_registry_policy_deny(db_session):
    registry = ToolRegistry()
    op = Operator(id="op2", name="op2", description="test", permission_level=PermissionLevel.read)
    tool = Tool(id="t1", name="dangerous_tool", required_permission=PermissionLevel.elevate)
    db_session.add(op)
    db_session.add(tool)
    await db_session.commit()

    # jit_wait_seconds=0: don't block waiting for an approval in tests
    res = await registry.execute("dangerous_tool", {}, operator=op, jit_wait_seconds=0)
    assert res.success is False
    assert "pending" in res.error.lower()
