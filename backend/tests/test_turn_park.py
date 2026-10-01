"""Parking a strict JIT wait instead of polling (#41)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from vigilus.config import get_settings
from vigilus.core.events import get_event_bus
from vigilus.core.sse import StreamBridge
from vigilus.core.tasks import get_task_registry
from vigilus.core.turn import execute_turn
from vigilus.core.turn_park import TurnParkContext, TurnParked
from vigilus.db.models import (
    Operator,
    OperatorTool,
    PermissionLevel,
    Provider,
    ProviderType,
    Session,
    Tool,
    ToolImplementationType,
    TrustMode,
    Turn,
    TurnStatus,
)
from vigilus.providers.base import AgentLLM, LLMResponse, ToolUse
from vigilus.tools.native import NATIVE_HANDLERS
from vigilus.tools.registry import ToolRegistry


@pytest_asyncio.fixture
async def strict_write_tool(db_session):
    provider = Provider(name="Park Provider", type="openai_compat", enabled=True)
    db_session.add(provider)
    await db_session.flush()
    operator = Operator(
        name="Park Operator",
        description="operator",
        provider_id=provider.id,
        trust_mode=TrustMode.strict,
        permission_level=PermissionLevel.read,
    )
    tool = Tool(
        name="park_probe",
        implementation_type=ToolImplementationType.native,
        required_permission=PermissionLevel.write,
        native_handler="park_probe",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    db_session.add_all([operator, tool])
    await db_session.flush()
    db_session.add(OperatorTool(operator_id=operator.id, tool_id=tool.id))
    session = Session(title="park", origin="web")
    db_session.add(session)
    await db_session.commit()
    turn = Turn(session_id=session.id, status=TurnStatus.running, origin="web")
    db_session.add(turn)
    await db_session.commit()
    return operator, session, turn


async def test_park_writes_checkpoint_and_raises(db_session, strict_write_tool, monkeypatch):
    operator, session, turn = strict_write_tool
    monkeypatch.setattr(get_settings(), "jit_wait_seconds", 1)
    ctx = TurnParkContext(session_id=session.id, origin="web", turn_id=turn.id)
    ctx.operator_id = operator.id
    events: list[dict] = []

    async def _record(payload: dict) -> None:
        events.append(payload)

    bus = get_event_bus()
    bus.subscribe("turn.parked", _record)
    try:
        with pytest.raises(TurnParked) as raised:
            await ToolRegistry().execute(
                "park_probe",
                {"path": "/etc/nginx"},
                operator=operator,
                session_id=session.id,
                park=ctx,
                tool_use_id="call_1",
                continuation={"mode": "text"},
                operator_messages=[{"role": "user", "content": "write it"}],
            )
    finally:
        bus.unsubscribe("turn.parked", _record)

    assert raised.value.turn_id == turn.id
    await db_session.refresh(turn)
    assert turn.status == TurnStatus.awaiting_approval
    assert turn.pending_call["tool"] == "park_probe"
    assert turn.pending_call["arguments"] == {"path": "/etc/nginx"}
    assert turn.pending_call["tool_use_id"] == "call_1"
    assert turn.jit_request_id
    assert turn.expires_at is not None
    assert events and events[0]["id"] == turn.id


async def test_kill_switch_uses_the_polling_wait(db_session, strict_write_tool, monkeypatch):
    operator, session, turn = strict_write_tool
    monkeypatch.setattr(get_settings(), "jit_park_resume", False)
    waited: list[str] = []

    async def _instant(self, request_id, wait_seconds, cancel_event=None):
        waited.append(request_id)
        return "denied"

    monkeypatch.setattr(ToolRegistry, "_wait_for_jit_resolution", _instant)
    ctx = TurnParkContext(session_id=session.id, turn_id=turn.id)
    result = await ToolRegistry().execute(
        "park_probe",
        {"path": "/etc/nginx"},
        operator=operator,
        session_id=session.id,
        park=ctx,
    )
    assert waited
    assert result.success is False
    await db_session.refresh(turn)
    assert turn.status == TurnStatus.running


async def test_without_park_context_uses_the_polling_wait(strict_write_tool, monkeypatch):
    operator, _session, _turn = strict_write_tool
    monkeypatch.setattr(get_settings(), "jit_wait_seconds", 1)
    waited: list[str] = []

    async def _instant(self, request_id, wait_seconds, cancel_event=None):
        waited.append(request_id)
        return "denied"

    monkeypatch.setattr(ToolRegistry, "_wait_for_jit_resolution", _instant)
    result = await ToolRegistry().execute(
        "park_probe",
        {"path": "/etc/nginx"},
        operator=operator,
    )
    assert waited
    assert result.success is False


class _Scripted(AgentLLM):
    supports_native_tools = False

    def __init__(self, orchestrator: list, operator: list):
        self.orchestrator = list(orchestrator)
        self.operator = list(operator)
        self.default_model = "scripted-model"

    async def complete(self, messages, tools=None, **kwargs) -> LLMResponse:
        if tools:
            reply = self.operator.pop(0)
            return reply if isinstance(reply, LLMResponse) else LLMResponse(content=reply)
        return LLMResponse(content=self.orchestrator.pop(0))

    async def test_connection(self) -> bool:
        return True


async def test_execute_turn_parks_strict_delegation(db_session, monkeypatch):
    from vigilus.core import orchestrator as orch

    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())
    monkeypatch.setattr(get_settings(), "jit_wait_seconds", 1)
    calls: list[dict] = []

    async def park_probe(arguments, operator, db=None):
        calls.append(arguments)
        return {"ok": True}

    monkeypatch.setitem(NATIVE_HANDLERS, "park_probe", park_probe)

    provider = Provider(
        name="scripted",
        type=ProviderType.openai_compat,
        base_url="http://scripted.invalid",
        default_model="scripted-model",
        is_default=True,
        enabled=True,
    )
    db_session.add(provider)
    await db_session.flush()
    operator = Operator(
        name="Ops",
        description="ops",
        provider_id=provider.id,
        trust_mode=TrustMode.strict,
        permission_level=PermissionLevel.read,
        delegatable=True,
        enabled=True,
    )
    tool = Tool(
        name="park_probe",
        description="probe",
        implementation_type=ToolImplementationType.native,
        required_permission=PermissionLevel.write,
        native_handler="park_probe",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    db_session.add_all([operator, tool])
    await db_session.flush()
    db_session.add(OperatorTool(operator_id=operator.id, tool_id=tool.id))
    session = Session(title="New Chat", origin="web")
    db_session.add(session)
    await db_session.commit()

    scripted = _Scripted(
        orchestrator=['{"delegate": "Ops", "task": "touch the file"}', "All done."],
        operator=[
            LLMResponse(
                content="",
                tool_uses=[
                    ToolUse(id="call_1", name="park_probe", arguments={"path": "/tmp/vigilus-park"})
                ],
            ),
            "Wrote the file.",
        ],
    )
    monkeypatch.setattr("vigilus.providers.registry.build_provider", lambda row: scripted)
    monkeypatch.setattr("vigilus.core.operator_runtime.build_provider", lambda row: scripted)

    running = get_task_registry().register(session.id, "Chat")
    bridge = StreamBridge()
    result = await execute_turn(
        db_session,
        session,
        "please touch it",
        bridge=bridge,
        cancel_event=running.cancel_event,
    )

    assert "awaiting approval" in result.text
    assert bridge._closed is True
    assert get_task_registry().get(session.id) is None
    assert calls == []
    parked = (
        await db_session.execute(
            select(Turn).where(
                Turn.session_id == session.id, Turn.status == TurnStatus.awaiting_approval
            )
        )
    ).scalar_one()
    assert parked.pending_call["tool"] == "park_probe"
    assert parked.pending_call["tool_use_id"] == "call_1"
    assert parked.operator_id == operator.id
    assert parked.operator_messages
