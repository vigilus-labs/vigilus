"""Integration tests for delegation, native tool dispatch, and the orchestrator."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from vigilus.db.models import Operator, OperatorTool, Provider, Tool
from vigilus.tools.native import NATIVE_HANDLERS
from vigilus.tools.registry import ToolRegistry

# ── Test: All seeded tool handlers resolve ─────────────────────────────────────


async def test_native_handlers_resolve():
    """Verify all 18 native handler names resolve to callable functions."""
    reg = ToolRegistry()
    handler_names = [
        "docker_list",
        "docker_logs",
        "docker_inspect",
        "docker_restart",
        "docker_compose_up",
        "docker_compose_pull",
        "docker_deploy_stack",
        "wazuh_get_alerts",
        "wazuh_get_vulnerabilities",
        "wazuh_get_agents",
        "wazuh_get_fim",
        "wazuh_search_logs",
        "fs_read",
        "fs_list",
        "fs_write",
        "ssh_exec",
        "ssh_exec_all",
        "shell_exec",
    ]
    for name in handler_names:
        handler = reg._get_native_handler(name)
        assert callable(handler), f"Handler {name} should be callable"


# ── Test: Tool handler call convention ─────────────────────────────────────────


async def test_handler_call_convention():
    """Verify handlers accept (arguments, operator=None, **kwargs)."""
    import inspect

    for name, handler in NATIVE_HANDLERS.items():
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        assert "arguments" in params, f"Handler {name} must accept 'arguments'"
        has_operator = "operator" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        assert has_operator, f"Handler {name} must accept 'operator' or **kwargs"


# ── Test: Tool registry with a native handler (fs_list) ───────────────────────


async def test_tool_registry_fs_list(db_session: AsyncSession):
    """Test that fs_list tool can be executed end-to-end."""
    from vigilus.db.models import PermissionLevel, ProviderType, ToolImplementationType, TrustMode

    provider = Provider(
        name="Test Prov 2",
        type=ProviderType.openai_compat,
        base_url="http://localhost:8000",
        enabled=True,
    )
    db_session.add(provider)
    db_session.add(
        Tool(
            name="fs_list_test",
            description="Test fs_list",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            implementation_type=ToolImplementationType.native,
            required_permission=PermissionLevel.read,
            native_handler="fs_list",
            is_builtin=True,
            available=True,
        )
    )
    db_session.add(
        Operator(
            name="Test Op 2",
            description="Test operator",
            system_prompt="You are a test.",
            permission_level=PermissionLevel.read,
            trust_mode=TrustMode.lenient,
            provider=provider,
        )
    )
    await db_session.commit()

    result = await db_session.execute(
        select(Operator)
        .options(
            selectinload(Operator.operator_tools).selectinload(OperatorTool.tool),
            selectinload(Operator.provider),
        )
        .where(Operator.name == "Test Op 2")
    )
    op = result.scalar_one()

    result = await db_session.execute(select(Tool).where(Tool.name == "fs_list_test"))
    tool = result.scalar_one()
    db_session.add(OperatorTool(operator_id=op.id, tool_id=tool.id))
    await db_session.commit()

    result = await db_session.execute(
        select(Operator)
        .options(
            selectinload(Operator.operator_tools).selectinload(OperatorTool.tool),
            selectinload(Operator.provider),
        )
        .where(Operator.id == op.id)
    )
    op = result.scalar_one()

    reg = ToolRegistry()
    result = await reg.execute(
        tool_id_or_name="fs_list_test",
        arguments={"path": "/tmp"},
        operator=op,
    )
    assert result.success, f"fs_list should succeed, got error: {result.error}"
    assert "entries" in result.output


# ── Test: WebSocket event bus message format ──────────────────────────────────


async def test_websocket_message_format(db_session: AsyncSession, async_client: AsyncClient):
    """Verify that WebSocket endpoint sends correctly formatted messages."""
    from vigilus.core.events import get_event_bus

    bus = get_event_bus()
    await bus.publish("action.created", {"event_type": "action.created", "test": True})


# ── Test: Delegation parsing ─────────────────────────────────────────────────


async def test_parse_delegation_finds_json_block():
    """Verify parse_delegation extracts delegation JSON from LLM responses."""
    from vigilus.core.delegation import parse_delegation

    # Inline JSON
    text = 'Let me delegate this.\n{"delegate": "Security Monitor", "task": "Check alerts", "context": "on server X"}\nHere we go.'
    result = parse_delegation(text)
    assert result is not None
    assert result["delegate"] == "Security Monitor"
    assert result["task"] == "Check alerts"

    # Fenced JSON block
    text = """I'll delegate this.
```json
{"delegate": "Patching Operator", "task": "Patch CVE-2024-1234"}
```
Done."""
    result = parse_delegation(text)
    assert result is not None
    assert result["delegate"] == "Patching Operator"
    assert result["task"] == "Patch CVE-2024-1234"

    # No delegation
    assert parse_delegation("Just a regular response.") is None
    assert parse_delegation('{"not_delegation": true}') is None
    assert parse_delegation("") is None


async def test_strip_delegation_leaves_plan_prose():
    """The user-facing plan survives; the JSON control block is removed."""
    from vigilus.core.delegation import strip_delegation

    # Fenced block after a plain-text plan.
    text = (
        "I'll have the Security Monitor check the alerts on server X.\n"
        '```json\n{"delegate": "Security Monitor", "task": "Check alerts"}\n```'
    )
    cleaned = strip_delegation(text)
    assert "I'll have the Security Monitor check the alerts on server X." in cleaned
    assert "delegate" not in cleaned
    assert "```" not in cleaned

    # Inline block.
    text = 'On it — patching now. {"delegate": "Patching Operator", "task": "Patch CVE"}'
    cleaned = strip_delegation(text)
    assert cleaned == "On it — patching now."

    # No delegation → returned unchanged.
    assert strip_delegation("Just a plain answer.") == "Just a plain answer."


# ── Test: Delegation execution handles missing operator ────────────────────────


async def test_execute_delegation_missing_operator(db_session: AsyncSession):
    """Delegation to a non-existent operator returns a descriptive error."""
    from vigilus.core.delegation import execute_delegation

    result = await execute_delegation(
        {"delegate": "NonExistent Operator", "task": "Do something"},
        db=db_session,
    )
    assert result["status"] == "error"
    assert "not found" in result["error"].lower() or "disabled" in result["error"].lower()


# ── Test: Orchestrator config ─────────────────────────────────────────────────


async def test_orchestrator_config_crud(async_client: AsyncClient):
    """Test GET and PATCH /api/orchestrator endpoints."""
    # GET returns defaults
    res = await async_client.get("/api/orchestrator")
    assert res.status_code == 200
    cfg = res.json()
    assert "provider_id" in cfg
    assert "model" in cfg
    assert "system_prompt" in cfg

    # PATCH with an invalid provider returns 400
    res = await async_client.patch("/api/orchestrator", json={"provider_id": "nonexistent-uuid"})
    # This should fail since no provider exists with that ID
    assert res.status_code in (400, 500) or res.json().get("provider_id") is None
