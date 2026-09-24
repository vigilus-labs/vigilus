"""Tests for cooperative cancellation of live chat turns."""

from __future__ import annotations

import asyncio

import pytest

from vigilus.core.tasks import TaskCancelled, await_cancelled, get_task_registry


@pytest.mark.asyncio
async def test_await_cancelled_stops_blocked_operation():
    started = asyncio.Event()
    operation_cancelled = asyncio.Event()
    cancel_event = asyncio.Event()

    async def blocked_operation():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            operation_cancelled.set()
            raise

    wait = asyncio.create_task(await_cancelled(blocked_operation(), cancel_event))
    await started.wait()
    cancel_event.set()

    with pytest.raises(TaskCancelled):
        await wait
    assert operation_cancelled.is_set()


def test_unregister_does_not_remove_newer_task_for_same_session():
    registry = get_task_registry()
    session_id = "task-registry-replacement-test"
    registry.unregister(session_id)

    older = registry.register(session_id, "Older task")
    newer = registry.register(session_id, "Newer task")
    registry.unregister(session_id, older.id)

    assert registry.get(session_id) is newer
    registry.unregister(session_id, newer.id)


@pytest.mark.asyncio
async def test_cancel_running_task_endpoint_signals_registry(async_client):
    registry = get_task_registry()
    session_id = "task-cancel-api-test"
    registry.unregister(session_id)
    task = registry.register(session_id, "Cancellable task")

    try:
        response = await async_client.post(f"/api/running-tasks/{session_id}/cancel")
        assert response.status_code == 200
        assert task.cancel_event.is_set()
        assert task.cancelling is True
        assert task.current_step == "Cancelling…"
    finally:
        registry.unregister(session_id, task.id)


# ── Loop detection + live tool-call events (issue #23) ────────────────────────


class _RecordingBridge:
    """Minimal StreamBridge stand-in that captures published events."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data):
        self.events.append((event_type, data))


def _make_runtime():
    """Build an OperatorRuntime around a stub operator + stub provider row."""
    from types import SimpleNamespace

    from vigilus.config import get_settings

    # get_settings is lru_cache'd — rebuild it so each test sees its own env
    # (monkeypatch.setenv alone would be shadowed by an instance cached by an
    # earlier test in the same process).
    get_settings.cache_clear()

    from vigilus.core.operator_runtime import OperatorRuntime
    from vigilus.db.models import ProviderType

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
    )
    runtime = OperatorRuntime(operator, fallback_provider=provider_row)
    return runtime, operator


async def _run_scripted(
    responses,
    monkeypatch=None,
    session_id=None,
    max_iterations=10,
):
    """Run OperatorRuntime with a scripted fake provider; returns results.

    ToolRegistry.execute is stubbed out — these tests exercise the runtime
    loop (loop detection, event payloads), not tool dispatch. This keeps
    them pure unit tests with no dependency on a migrated test database.
    """
    from vigilus.core.operator_runtime import OperatorRuntime
    from vigilus.providers.base import LLMMessage
    from vigilus.tools.registry import ToolResult

    runtime, _operator = _make_runtime()

    class FakeProvider:
        default_model = "stub-model"
        calls = 0

        async def complete(self, **kwargs):
            resp = responses[min(FakeProvider.calls, len(responses) - 1)]
            FakeProvider.calls += 1
            return resp

    runtime.provider = FakeProvider()

    executed: list[str] = []

    async def _stub_execute(self, **kwargs):
        executed.append(kwargs["tool_id_or_name"])
        return ToolResult(success=True, output="stubbed")

    async def _nop_tools(self):
        return []

    async def _nop_prompt(self, tools):
        return None

    monkeypatch.setattr(OperatorRuntime, "_get_tools", _nop_tools)
    monkeypatch.setattr(OperatorRuntime, "_build_system_prompt", _nop_prompt)
    monkeypatch.setattr("vigilus.core.operator_runtime.ToolRegistry.execute", _stub_execute)

    bridge = _RecordingBridge()
    messages, tool_history = await runtime.run(
        [LLMMessage(role="user", content="do it")],
        session_id=session_id,
        max_iterations=max_iterations,
        bridge=bridge,
    )
    return messages, tool_history, bridge, executed


@pytest.mark.asyncio
async def test_loop_detection_aborts_after_threshold(monkeypatch):
    from vigilus.providers.base import LLMResponse, ToolUse

    def repeating():
        while True:
            yield LLMResponse(
                content="working",
                tool_uses=[ToolUse(id="t1", name="fs_list", arguments={"path": "/tmp"})],
                usage={},
            )

    gen = repeating()
    responses = [next(gen) for _ in range(10)]

    messages, tool_history, bridge, executed = await _run_scripted(responses, monkeypatch)

    loop_entries = [t for t in tool_history if t.get("loop_detected")]
    assert loop_entries, "expected a loop_detected entry in tool history"
    assert loop_entries[0]["tool"] == "fs_list"
    assert loop_entries[0]["count"] == 3

    # Two calls executed; the third (identical) one was blocked before dispatch.
    tool_call_events = [e for e in bridge.events if e[0] == "tool_call"]
    assert len(tool_call_events) == 2
    loop_events = [e for e in bridge.events if e[0] == "loop_detected"]
    assert len(loop_events) == 1
    assert loop_events[0][1]["tool"] == "fs_list"
    assert loop_events[0][1]["count"] == 3

    # A synthetic final assistant message explains the abort.
    final_text = [str(m.content) for m in messages if m.role == "assistant" and m.content]
    assert any("loop" in t.lower() for t in final_text)


@pytest.mark.asyncio
async def test_loop_detection_resets_on_different_arguments(monkeypatch):
    from vigilus.providers.base import LLMResponse, ToolUse

    responses = [
        LLMResponse(
            content="working",
            tool_uses=[ToolUse(id=f"t{i}", name="fs_list", arguments={"path": p})],
            usage={},
        )
        for i, p in enumerate(["/a", "/b", "/a", "/b", "/a"])
    ]
    # Final plain response ends the loop cleanly after the 5 tool iterations.
    responses.append(LLMResponse(content="done", usage={}))

    messages, tool_history, bridge, executed = await _run_scripted(
        responses, monkeypatch, max_iterations=10
    )

    assert not [t for t in tool_history if t.get("loop_detected")]
    assert not [e for e in bridge.events if e[0] == "loop_detected"]
    tool_call_events = [e for e in bridge.events if e[0] == "tool_call"]
    assert len(tool_call_events) == 5


@pytest.mark.asyncio
async def test_loop_detection_can_be_disabled(monkeypatch):
    from vigilus.providers.base import LLMResponse, ToolUse

    monkeypatch.setenv("VIGILUS_LOOP_DETECTION_THRESHOLD", "0")
    responses = [
        LLMResponse(
            content="working",
            tool_uses=[ToolUse(id="t1", name="fs_list", arguments={"path": "/tmp"})],
            usage={},
        )
        for _ in range(6)
    ]

    messages, tool_history, bridge, executed = await _run_scripted(
        responses, monkeypatch, max_iterations=6
    )

    assert not [t for t in tool_history if t.get("loop_detected")]
    tool_call_events = [e for e in bridge.events if e[0] == "tool_call"]
    assert len(tool_call_events) == 6


@pytest.mark.asyncio
async def test_tool_call_events_redact_secrets(monkeypatch):
    from vigilus.providers.base import LLMResponse, ToolUse

    sensitive_args = {
        "path": "/tmp",
        "password": "hunter2",
        "jit_token": "sig:hmac-secret",
        "ssh_key": "PRIVATE",
    }
    responses = [
        LLMResponse(
            content="working",
            tool_uses=[ToolUse(id="t1", name="ssh_exec", arguments=sensitive_args)],
            usage={},
        ),
        LLMResponse(content="done", usage={}),
    ]

    messages, tool_history, bridge, executed = await _run_scripted(responses, monkeypatch)

    tool_call_events = [e for e in bridge.events if e[0] == "tool_call"]
    assert len(tool_call_events) == 1
    payload = tool_call_events[0][1]

    # Redacted full args…
    assert payload["args"]["password"] == "***REDACTED***"
    assert payload["args"]["jit_token"] == "***REDACTED***"
    assert payload["args"]["ssh_key"] == "***REDACTED***"
    assert payload["args"]["path"] == "/tmp"
    # …and the compact preview carries no raw secret either.
    assert "hunter2" not in payload["args_preview"]
    assert "sig:hmac-secret" not in payload["args_preview"]
    assert "***REDACTED***" in payload["args_preview"]
    # Iteration progress rides along on the same event.
    assert payload["iteration"] == 1
    assert payload["max_iterations"] == 10

    # Nothing unredacted anywhere in the whole event stream.
    for _evt, data in bridge.events:
        assert "hunter2" not in str(data)
        assert "sig:hmac-secret" not in str(data)


@pytest.mark.asyncio
async def test_running_task_detail_includes_usage(db_session, async_client):
    from vigilus.core.llm_usage import record_llm_usage
    from vigilus.db.models import UsageActorType

    registry = get_task_registry()
    session_id = "running-task-usage-test"
    registry.unregister(session_id)
    task = registry.register(session_id, "Usage task")

    try:
        await record_llm_usage(
            usage={"input_tokens": 100, "output_tokens": 40},
            actor_type=UsageActorType.orchestrator,
            session_id=session_id,
        )
        await record_llm_usage(
            usage={"input_tokens": 60, "output_tokens": 20},
            actor_type=UsageActorType.operator,
            session_id=session_id,
        )

        detail = await async_client.get(f"/api/running-tasks/{session_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["running"] is True
        assert body["tokens_in"] == 160
        assert body["tokens_out"] == 60

        listing = await async_client.get("/api/running-tasks")
        assert listing.status_code == 200
        row = next(t for t in listing.json() if t["session_id"] == session_id)
        assert row["tokens_in"] == 160
        assert row["tokens_out"] == 60
    finally:
        registry.unregister(session_id, task.id)


# ── Stalled tool calls must not outlive a cancel request ──────────────────────


@pytest.mark.asyncio
async def test_cancel_interrupts_hung_tool_call(db_session, monkeypatch):
    """A tool that never returns must not keep its turn "running" after cancel."""
    from vigilus.db.models import (
        Operator,
        PermissionLevel,
        Tool,
        ToolImplementationType,
        TrustMode,
    )
    from vigilus.tools.registry import ToolRegistry

    op = Operator(
        name="Hung Tool Operator",
        description="test",
        permission_level=PermissionLevel.exec,
        trust_mode=TrustMode.strict,
    )
    tool = Tool(
        name="hung_tool",
        description="never returns",
        implementation_type=ToolImplementationType.native,
        required_permission=PermissionLevel.read,
        native_handler="hung_tool",
        input_schema={"type": "object", "properties": {}},
    )
    db_session.add_all([op, tool])
    await db_session.commit()
    await db_session.refresh(op)

    started = asyncio.Event()
    handler_cancelled = asyncio.Event()

    async def hung_handler(arguments, operator):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            handler_cancelled.set()
            raise

    registry = ToolRegistry()
    monkeypatch.setattr(registry, "_get_native_handler", lambda _path: hung_handler)

    cancel_event = asyncio.Event()
    call = asyncio.create_task(
        registry.execute(tool.name, {}, operator=op, cancel_event=cancel_event)
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    cancel_event.set()

    result = await asyncio.wait_for(call, timeout=5)
    assert result.success is False
    assert "cancelled" in (result.error or "").lower()
    assert handler_cancelled.is_set()


@pytest.mark.asyncio
async def test_ssh_exec_times_out_when_command_never_exits(monkeypatch):
    """A remote command that never exits (tail -f, a prompt) must honour timeout."""
    import threading
    import time

    from vigilus.tools.native import ssh

    released = threading.Event()

    class _NeverExitsChannel:
        def recv_ready(self):
            return False

        def recv_stderr_ready(self):
            return False

        def exit_status_ready(self):
            return False

        def recv_exit_status(self):
            # Blocks like paramiko does for a command that never exits; bounded
            # here only so a regression fails the test instead of hanging it.
            released.wait(10)
            return -1

        def close(self):
            released.set()

    class _Stream:
        def __init__(self, channel):
            self.channel = channel

        def read(self):
            return b""

    class _FakeClient:
        def __init__(self):
            self.channel = _NeverExitsChannel()

        def exec_command(self, command, timeout=None):
            return None, _Stream(self.channel), _Stream(self.channel)

        def close(self):
            released.set()

    monkeypatch.setattr(ssh, "_ssh_connect_sync", lambda *a, **kw: _FakeClient())

    started = time.monotonic()
    result = await asyncio.wait_for(
        ssh.ssh_exec({"host": "example.test", "command": "tail -f /var/log/x", "timeout": 1}),
        timeout=15,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"ssh_exec ignored its 1s timeout (took {elapsed:.1f}s)"
    assert "timed out" in (result.get("error") or "").lower()
