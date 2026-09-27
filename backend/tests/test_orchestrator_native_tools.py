"""Native tool delegation and parallel delegations (issues #39 and #40).

The orchestrator gets real tools — ``delegate``, ``web_search``, ``web_fetch``,
``remember`` — instead of regex-parsed JSON blocks. Several ``delegate`` calls
in one response run concurrently, each with its own DB session, under a shared
cancel event. Providers without the native-tools capability keep the parsed
text path.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from vigilus.core.orchestrator_loop import load_db_messages_as_llm, run_orchestrator
from vigilus.core.orchestrator_tools import (
    DELEGATE_TOOL,
    REMEMBER_TOOL,
    WEB_FETCH_TOOL,
    WEB_SEARCH_TOOL,
    build_orchestrator_tools,
    orchestrator_uses_native_tools,
)
from vigilus.core.tasks import TaskCancelled
from vigilus.db.models import MessageRole, Operator, PermissionLevel
from vigilus.providers.base import AgentLLM, LLMMessage, LLMResponse, ToolUse


class NativeProvider(AgentLLM):
    """Returns scripted LLMResponses; records every request it saw."""

    def __init__(self, responses: list[LLMResponse], *, native: bool = True):
        self.responses = list(responses)
        self.calls: list[dict] = []
        if not native:
            self.supports_native_tools = False

    async def complete(self, messages, **kwargs) -> LLMResponse:  # pragma: no cover
        raise AssertionError("the orchestrator should use complete_streaming")

    async def complete_streaming(self, messages, *, tools=None, **kwargs) -> LLMResponse:
        self.calls.append({"tools": tools, "messages": list(messages)})
        return self.responses.pop(0)

    async def test_connection(self) -> bool:
        return True


def _delegate_response(*calls: tuple[str, dict]) -> LLMResponse:
    return LLMResponse(
        content="Delegating now.",
        tool_uses=[ToolUse(id=tid, name=DELEGATE_TOOL, arguments=args) for tid, args in calls],
        usage={"input_tokens": 10, "output_tokens": 5},
    )


async def _add_operator(db_session, name: str) -> Operator:
    op = Operator(
        id=f"op-{name.lower().replace(' ', '-')}",
        name=name,
        description="test",
        permission_level=PermissionLevel.read,
        enabled=True,
        delegatable=True,
    )
    db_session.add(op)
    await db_session.commit()
    return op


async def test_delegate_tool_carries_operator_enum(db_session, monkeypatch):
    """The delegate tool's operator param enumerates enabled delegatable operators."""
    from vigilus.config import get_settings

    await _add_operator(db_session, "Systems Operator")
    await _add_operator(db_session, "Security Monitor")
    hidden = await _add_operator(db_session, "Vigilus")
    hidden.delegatable = False
    await db_session.commit()

    tools = await build_orchestrator_tools(
        db_session, search_enabled=get_settings().search_enabled
    )
    delegate = next(t for t in tools if t.name == DELEGATE_TOOL)
    assert set(delegate.input_schema["properties"]["operator"]["enum"]) == {
        "Systems Operator",
        "Security Monitor",
    }

    # Without search, the research tools must not be offered.
    tools_no_search = await build_orchestrator_tools(db_session, search_enabled=False)
    names = {t.name for t in tools_no_search}
    assert WEB_SEARCH_TOOL not in names and WEB_FETCH_TOOL not in names
    assert REMEMBER_TOOL in names


async def test_parallel_delegations_run_concurrently(db_session, monkeypatch):
    """Two delegate calls in one response overlap instead of running serially."""
    both_in_flight = asyncio.Event()
    active = 0
    max_active = 0
    received_tasks: list[str] = []

    async def fake_delegation(request, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            both_in_flight.set()
        try:
            await asyncio.wait_for(both_in_flight.wait(), 5)
            received_tasks.append(request["task"])
        finally:
            active -= 1
        return {
            "status": "success",
            "operator": request["operator"],
            "response": f"{request['operator']} report",
            "tool_calls": [],
        }

    monkeypatch.setattr("vigilus.core.delegation.execute_delegation", fake_delegation)

    await _add_operator(db_session, "Systems Operator")
    await _add_operator(db_session, "Security Monitor")

    provider = NativeProvider(
        [
            _delegate_response(
                ("t1", {"operator": "Systems Operator", "task": "run `apt list --upgradable`"}),
                (
                    "t2",
                    {
                        "operator": "Security Monitor",
                        "task": 'parse {"alerts": ["CVE-2026-1234"]} from wazuh',
                    },
                ),
            ),
            LLMResponse(content="All checks complete.", usage={"input_tokens": 5, "output_tokens": 5}),
        ]
    )

    new_messages = await run_orchestrator(
        [LLMMessage(role="user", content="check updates and alerts")],
        provider,
        "system",
        db=db_session,
        session_id="sess-native",
    )

    assert max_active == 2, "delegations must overlap, not run one-by-one"
    assert any("apt list --upgradable" in t for t in received_tasks)
    assert any('{"alerts": ["CVE-2026-1234"]}' in t for t in received_tasks)

    # Assistant message carries the tool_use blocks; results carry their ids.
    assistant = next(m for m in new_messages if m["role"] == "assistant")
    assert [tc["id"] for tc in assistant["content"]["tool_calls"]] == ["t1", "t2"]
    tool_rows = [m for m in new_messages if m["role"] == "tool"]
    assert {m["content"]["tool_use_id"] for m in tool_rows} == {"t1", "t2"}

    # The follow-up provider call sees proper tool_use/tool_result pairs.
    second = provider.calls[1]["messages"]
    tool_msgs = [m for m in second if m.role == "tool"]
    assert {m.tool_use_id for m in tool_msgs} == {"t1", "t2"}


async def test_delegation_budget_refuses_extra_calls(db_session, monkeypatch):
    """Delegates beyond the remaining budget are refused, not executed."""
    executed: list[str] = []

    async def fake_delegation(request, **kwargs):
        executed.append(request["operator"])
        return {"status": "success", "operator": request["operator"], "response": "ok"}

    monkeypatch.setattr("vigilus.core.delegation.execute_delegation", fake_delegation)

    provider = NativeProvider(
        [
            _delegate_response(
                ("t1", {"operator": "Ops", "task": "first"}),
                ("t2", {"operator": "Ops", "task": "second"}),
            ),
            LLMResponse(content="Wrapping up.", usage={"input_tokens": 5, "output_tokens": 5}),
        ]
    )

    new_messages = await run_orchestrator(
        [LLMMessage(role="user", content="two things")],
        provider,
        "system",
        db=db_session,
        session_id="sess-budget",
        max_delegations=1,
    )

    assert executed == ["Ops"]
    refusals = [
        m
        for m in new_messages
        if m["role"] == "tool" and "budget" in str(m["content"].get("result", ""))
    ]
    assert len(refusals) == 1
    assert refusals[0]["content"]["tool_use_id"] == "t2"


async def test_cancelled_delegation_stops_the_turn(db_session, monkeypatch):
    """A TaskCancelled in one parallel branch ends the turn cleanly."""

    async def fake_delegation(request, **kwargs):
        raise TaskCancelled()

    monkeypatch.setattr("vigilus.core.delegation.execute_delegation", fake_delegation)

    provider = NativeProvider(
        [
            _delegate_response(("t1", {"operator": "Ops", "task": "hang"})),
            LLMResponse(content="unreachable", usage={}),
        ]
    )

    new_messages = await run_orchestrator(
        [LLMMessage(role="user", content="cancel me")],
        provider,
        "system",
        db=db_session,
        session_id="sess-cancel",
    )

    assert len(provider.calls) == 1, "no further LLM call after cancellation"
    assert any("cancelled" in str(m["content"]).lower() for m in new_messages)


async def test_text_provider_keeps_parsed_path_and_gets_no_tools(db_session, monkeypatch):
    """A provider without the capability flag gets tools=None; text delegation still works."""
    import json as _json

    reply = (
        "Plan.\n\n```json\n"
        + _json.dumps({"delegate": "Ops", "task": "check"})
        + "\n```"
    )

    async def fake_delegation(request, **kwargs):
        return {"status": "success", "operator": request["operator"], "response": "done"}

    monkeypatch.setattr("vigilus.core.delegation.execute_delegation", fake_delegation)

    provider = NativeProvider(
        [
            LLMResponse(content=reply, usage={"input_tokens": 5, "output_tokens": 5}),
            LLMResponse(content="All done.", usage={"input_tokens": 5, "output_tokens": 5}),
        ],
        native=False,
    )
    assert orchestrator_uses_native_tools(provider) is False

    await run_orchestrator(
        [LLMMessage(role="user", content="check")],
        provider,
        "system",
        db=db_session,
        session_id="sess-text",
    )

    assert provider.calls[0]["tools"] is None


def test_history_reconstruction_pairs_native_tool_messages():
    """Persisted native rows rebuild as tool_use/tool_result pairs; legacy rows stay framed."""
    rows = [
        SimpleNamespace(role=MessageRole.user, content="do the thing"),
        SimpleNamespace(
            role=MessageRole.assistant,
            content={
                "text": "On it.",
                "delegation": [{"operator": "Ops", "task": "t"}],
                "tool_calls": [
                    {"type": "tool_use", "id": "t1", "name": "delegate", "input": {}}
                ],
            },
            operator_id=None,
        ),
        SimpleNamespace(
            role=MessageRole.tool,
            content={
                "operator": "Ops",
                "result": "report body",
                "status": "success",
                "tool": "delegate",
                "tool_use_id": "t1",
            },
            operator_id="Ops",
        ),
        # Legacy row (pre-native): no tool_use_id — must stay a framed user msg.
        SimpleNamespace(
            role=MessageRole.tool,
            content={"operator": "Ops", "result": "old report", "status": "success"},
            operator_id="Ops",
        ),
    ]

    msgs = load_db_messages_as_llm(rows)

    assistant = msgs[1]
    assert assistant.role == "assistant"
    assert assistant.content == "On it."
    assert assistant.tool_calls[0]["id"] == "t1"

    native_result = msgs[2]
    assert native_result.role == "tool"
    assert native_result.tool_use_id == "t1"
    assert native_result.content == "report body"

    legacy = msgs[3]
    assert legacy.role == "user"
    assert "DELEGATION RESULT" in str(legacy.content)
