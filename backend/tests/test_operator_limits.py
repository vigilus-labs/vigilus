"""Tool-output cap and iteration-limit summary (issues #35 and #36)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vigilus.core.operator_runtime import cap_tool_output
from vigilus.db.models import ProviderType
from vigilus.providers.base import LLMMessage, LLMResponse, ToolUse
from vigilus.tools.registry import ToolResult


def test_cap_tool_output_keeps_head_tail_and_marker():
    text = "HEAD-" + ("x" * 5000) + "-TAIL"
    capped = cap_tool_output(text, 400)

    assert len(capped) <= 400
    assert capped.startswith("HEAD-")
    assert capped.endswith("-TAIL")
    assert "bytes truncated" in capped
    assert cap_tool_output(text, 0) == text
    assert cap_tool_output("short", 100) == "short"

    tiny = cap_tool_output("abcdefghij", 4)
    assert len(tiny) <= 4


def test_format_delegation_result_flags_iteration_limit():
    from vigilus.core.orchestrator_loop import format_delegation_result

    text = format_delegation_result(
        {
            "status": "success",
            "operator": "Recon",
            "response": "found 2 hosts",
            "tool_calls": [],
            "iteration_limit_reached": True,
        }
    )
    assert "iteration limit" in text
    assert "found 2 hosts" in text


def _runtime():
    from vigilus.config import get_settings
    from vigilus.core.operator_runtime import OperatorRuntime

    get_settings.cache_clear()
    provider_row = SimpleNamespace(
        id="prov-1",
        name="Stub Provider",
        type=ProviderType.openai_compat,
        default_model="stub-model",
        api_key=None,
        base_url="http://localhost:9",
        extra_headers=None,
    )
    operator = SimpleNamespace(
        id="op-1",
        name="StubOp",
        model=None,
        provider=provider_row,
        operator_tools=[],
        system_prompt=None,
        soul=None,
        max_iterations=None,
        monthly_budget_usd=None,
    )
    runtime = OperatorRuntime(operator, fallback_provider=provider_row)
    return runtime, operator


async def _run(
    monkeypatch,
    responses,
    *,
    max_iterations=None,
    tool_output="stubbed",
    operator_limit=None,
    output_cap=None,
):
    from vigilus.config import get_settings
    from vigilus.core.operator_runtime import OperatorRuntime

    runtime, operator = _runtime()
    if operator_limit is not None:
        operator.max_iterations = operator_limit
    if output_cap is not None:
        monkeypatch.setattr(get_settings(), "tool_output_max_chars", output_cap)
    seen_tools: list = []

    class FakeProvider:
        default_model = "stub-model"

        async def complete(self, **kwargs):
            seen_tools.append(kwargs.get("tools"))
            resp = responses[min(len(seen_tools) - 1, len(responses) - 1)]
            return resp

    runtime.provider = FakeProvider()

    async def _stub_execute(self, **kwargs):
        return ToolResult(success=True, output=tool_output)

    async def _nop_tools(self):
        return []

    async def _nop_prompt(self, tools):
        return None

    monkeypatch.setattr(OperatorRuntime, "_get_tools", _nop_tools)
    monkeypatch.setattr(OperatorRuntime, "_build_system_prompt", _nop_prompt)
    monkeypatch.setattr("vigilus.core.operator_runtime.ToolRegistry.execute", _stub_execute)

    messages, tool_history = await runtime.run(
        [LLMMessage(role="user", content="do it")],
        session_id=None,
        max_iterations=max_iterations,
    )
    return messages, tool_history, seen_tools, operator


def _tool_response(i: int = 1) -> LLMResponse:
    return LLMResponse(
        content="Let me check…",
        tool_uses=[ToolUse(id=f"t{i}", name="fs_read", arguments={"path": f"/tmp/{i}"})],
        usage={},
    )


@pytest.mark.asyncio
async def test_iteration_limit_asks_for_summary_without_tools(monkeypatch):
    messages, tool_history, seen_tools, operator = await _run(
        monkeypatch,
        [_tool_response(1), _tool_response(2), LLMResponse(content="partial findings", usage={})],
        max_iterations=2,
    )

    assert len(seen_tools) == 3
    assert seen_tools[0] == []
    assert seen_tools[2] is None
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "partial findings"
    assert any(t.get("iteration_limit_reached") for t in tool_history)
    assert operator.max_iterations is None


@pytest.mark.asyncio
async def test_empty_summary_still_reports_the_limit(monkeypatch):
    messages, tool_history, seen_tools, _operator = await _run(
        monkeypatch,
        [_tool_response(1), LLMResponse(content="   ", usage={})],
        max_iterations=1,
    )

    assert seen_tools[-1] is None
    assert "Iteration limit reached" in messages[-1].content
    assert any(t.get("iteration_limit_reached") for t in tool_history)


@pytest.mark.asyncio
async def test_finished_run_does_not_take_the_extra_call(monkeypatch):
    messages, tool_history, seen_tools, _operator = await _run(
        monkeypatch,
        [_tool_response(1), LLMResponse(content="all done", usage={})],
        max_iterations=5,
    )

    assert len(seen_tools) == 2
    assert all(tools is not None for tools in seen_tools)
    assert messages[-1].content == "all done"
    assert not any(t.get("iteration_limit_reached") for t in tool_history)


@pytest.mark.asyncio
async def test_operator_limit_overrides_the_global_default(monkeypatch):
    messages, tool_history, seen_tools, _operator = await _run(
        monkeypatch,
        [_tool_response(1), LLMResponse(content="stopped early", usage={})],
        operator_limit=1,
    )

    assert len(seen_tools) == 2
    assert seen_tools[-1] is None
    assert messages[-1].content == "stopped early"
    assert any(t.get("iteration_limit_reached") for t in tool_history)


@pytest.mark.asyncio
async def test_oversized_tool_output_is_truncated_for_the_model(monkeypatch):
    raw = "HEAD-" + ("x" * 5000) + "-TAIL"
    messages, _history, _seen, _operator = await _run(
        monkeypatch,
        [_tool_response(1), LLMResponse(content="done", usage={})],
        max_iterations=3,
        tool_output=raw,
        output_cap=400,
    )

    tool_msgs = [m for m in messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    content = str(tool_msgs[0].content)
    assert len(content) <= 400
    assert content.startswith("HEAD-")
    assert content.endswith("-TAIL")
    assert "bytes truncated" in content
    assert raw not in content


@pytest.mark.asyncio
async def test_action_output_is_stored_and_omitted_from_the_list(
    db_session, async_client, monkeypatch
):
    from sqlalchemy import select

    from vigilus.db.models import Action, Operator
    from vigilus.db.seed import VIGILUS_PRINCIPAL_NAME, run_seed
    from vigilus.search.base import SearchResult
    from vigilus.tools.registry import ToolRegistry

    await run_seed(db_session)
    principal = (
        await db_session.execute(select(Operator).where(Operator.name == VIGILUS_PRINCIPAL_NAME))
    ).scalar_one()

    import vigilus.tools.native.search as search_mod

    class _FakeBackend:
        async def search(self, query, *, max_results=5):
            return [SearchResult(title="Doc", url="https://docs.example/x", snippet="hello")]

    monkeypatch.setattr(search_mod, "build_search_backend", lambda cfg: _FakeBackend())

    result = await ToolRegistry().execute(
        tool_id_or_name="web_search",
        arguments={"query": "docs example"},
        operator=principal,
    )
    assert result.success

    listed = await async_client.get("/api/actions", params={"tool_name": "web_search"})
    assert listed.status_code == 200
    rows = listed.json()
    end = next(row for row in rows if row["event"] == "tool_call_end")
    assert end["output"] is None

    detail = await async_client.get(f"/api/actions/{end['id']}")
    assert detail.status_code == 200
    assert detail.json()["output"] == result.output

    stored = await db_session.get(Action, end["id"])
    assert stored is not None
    assert stored.output == result.output
