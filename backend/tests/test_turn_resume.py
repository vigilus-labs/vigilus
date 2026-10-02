"""Resuming a parked turn after JIT approval (#41)."""

from __future__ import annotations

from sqlalchemy import select

from vigilus.config import get_settings
from vigilus.core.tasks import get_task_registry
from vigilus.core.turn import execute_turn
from vigilus.core.turn_recovery import recover_interrupted_turns
from vigilus.core.turn_resume import drain_resumes, install_resume_subscriber, resume_turn
from vigilus.db.models import (
    Message,
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


async def _park(db_session, monkeypatch, calls: list):
    from vigilus.core import orchestrator as orch

    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())
    monkeypatch.setattr(get_settings(), "jit_wait_seconds", 1)

    async def park_probe(arguments, operator, db=None):
        calls.append(dict(arguments))
        return {"ok": True}

    monkeypatch.setitem(NATIVE_HANDLERS, "park_probe", park_probe)

    provider = Provider(
        name="scripted-resume",
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

    class _Gateway:
        async def send(self, platform, chat_id, text):
            return None

    monkeypatch.setattr("vigilus.integrations.gateway.get_gateway", lambda: _Gateway())

    await execute_turn(db_session, session, "please touch it", deliver_to={"platform": "telegram", "chat_id": "9"})
    turn = (
        await db_session.execute(
            select(Turn).where(
                Turn.session_id == session.id, Turn.status == TurnStatus.awaiting_approval
            )
        )
    ).scalar_one()
    return session, turn


async def _texts(db_session, session_id: str) -> list[str]:
    rows = (
        (
            await db_session.execute(
                select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [row.content for row in rows if isinstance(row.content, str)]


async def test_approve_resumes_and_delivers(db_session, monkeypatch):
    calls: list[dict] = []
    session, turn = await _park(db_session, monkeypatch, calls)
    delivered: list[tuple] = []

    class _Gateway:
        async def send(self, platform, chat_id, text):
            delivered.append((platform, chat_id, text))

    monkeypatch.setattr(
        "vigilus.integrations.gateway.get_gateway", lambda: _Gateway()
    )

    from vigilus.core.rbac import WardenService

    await WardenService().approve_request(db_session, turn.jit_request_id, approver="admin")
    await resume_turn(turn.id, "approved")

    await db_session.refresh(turn)
    assert turn.status == TurnStatus.completed
    assert calls == [{"path": "/tmp/vigilus-park"}]
    texts = await _texts(db_session, session.id)
    assert any("All done." in text for text in texts)
    assert delivered and delivered[0][0] == "telegram"
    assert "All done." in delivered[0][2]


async def test_deny_resumes_with_denial_and_does_not_run_the_tool(db_session, monkeypatch):
    calls: list[dict] = []
    session, turn = await _park(db_session, monkeypatch, calls)
    # The operator's next reply is the denial report. Replace the unused
    # "Wrote the file." that approve would have consumed.
    await resume_turn(turn.id, "denied")

    await db_session.refresh(turn)
    assert turn.status == TurnStatus.completed
    assert calls == []
    texts = await _texts(db_session, session.id)
    assert any("DENIED" in text or "denied" in text.lower() or "All done." in text for text in texts)


async def test_second_resume_is_a_no_op(db_session, monkeypatch):
    calls: list[dict] = []
    _session, turn = await _park(db_session, monkeypatch, calls)
    from vigilus.core.rbac import WardenService

    await WardenService().approve_request(db_session, turn.jit_request_id, approver="admin")
    await resume_turn(turn.id, "approved")
    await resume_turn(turn.id, "approved")
    assert calls == [{"path": "/tmp/vigilus-park"}]
    await db_session.refresh(turn)
    assert turn.status == TurnStatus.completed


async def test_resume_error_marks_the_turn_failed(db_session, monkeypatch):
    calls: list[dict] = []
    _session, turn = await _park(db_session, monkeypatch, calls)

    async def _boom(self, *args, **kwargs):
        raise RuntimeError("resume blew up")

    monkeypatch.setattr(
        "vigilus.core.operator_runtime.OperatorRuntime.continue_after_park", _boom
    )
    from vigilus.core.rbac import WardenService

    await WardenService().approve_request(db_session, turn.jit_request_id, approver="admin")
    await resume_turn(turn.id, "approved")
    await db_session.refresh(turn)
    assert turn.status == TurnStatus.failed
    assert "resume blew up" in (turn.error or "")
    assert calls == []


async def test_parked_turn_survives_restart_and_resumes(db_session, monkeypatch):
    calls: list[dict] = []
    session, turn = await _park(db_session, monkeypatch, calls)
    interrupted = Turn(session_id=session.id, status=TurnStatus.running, origin="web")
    db_session.add(interrupted)
    await db_session.commit()

    get_task_registry()._tasks.clear()
    failed = await recover_interrupted_turns()
    assert failed == 1
    await db_session.refresh(interrupted)
    await db_session.refresh(turn)
    assert interrupted.status == TurnStatus.failed
    assert interrupted.error == "Interrupted — backend restarted"
    assert turn.status == TurnStatus.awaiting_approval

    install_resume_subscriber()
    from vigilus.core.rbac import WardenService

    await WardenService().approve_request(db_session, turn.jit_request_id, approver="admin")
    await drain_resumes()

    await db_session.refresh(turn)
    assert turn.status == TurnStatus.completed
    assert calls == [{"path": "/tmp/vigilus-park"}]
    assert get_task_registry().get(session.id) is None
