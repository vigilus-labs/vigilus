"""The orchestrator streams its prose to the chat while the work runs.

The plan Vigilus announces ("I'll have the Systems Operator …") has to reach
the user as it is written — not after the delegation it announces has already
finished — and the delegation JSON that rides along with it must never appear
on screen.
"""

from __future__ import annotations

import json

from vigilus.api.chat import _run_orchestrator
from vigilus.api.sse import EVT_TEXT_CHUNK, EVT_TEXT_DELTA, StreamBridge
from vigilus.providers.base import AgentLLM, LLMMessage, LLMResponse, emit_text

PLAN = "I'll have the Systems Operator SSH into every server and check for updates."
DELEGATION = json.dumps({"delegate": "Systems Operator", "task": "check for updates"})
REPLY = f"{PLAN}\n\n```json\n{DELEGATION}\n```"


class StreamingProvider(AgentLLM):
    """Emits each reply one word at a time, like a real streaming endpoint."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.delegation_seen_at: int | None = None

    async def complete(self, messages, **kwargs) -> LLMResponse:  # pragma: no cover
        raise AssertionError("the orchestrator should stream instead")

    async def complete_streaming(self, messages, *, on_text=None, **kwargs) -> LLMResponse:
        reply = self.replies.pop(0)
        for word in reply.split(" "):
            await emit_text(on_text, word + " ")
        return LLMResponse(content=reply, usage={"input_tokens": 10, "output_tokens": 5})

    async def test_connection(self) -> bool:
        return True


class RecordingBridge(StreamBridge):
    """A bridge that keeps every event, and lets a test freeze them in time."""

    def __init__(self):
        super().__init__()
        self.events: list[tuple[str, dict]] = []

    def publish(self, event: str, data: dict | None = None) -> None:
        self.events.append((event, data or {}))
        super().publish(event, data)

    def texts(self, event_type: str) -> list[str]:
        return [d.get("text", "") for e, d in self.events if e == event_type]


async def test_plan_is_streamed_before_the_delegation_runs(db_session, monkeypatch):
    bridge = RecordingBridge()
    chunks_before_delegation: list[str] = []

    async def _fake_delegation(*args, **kwargs):
        # Snapshot what the user could already see at the moment the operator
        # starts working — this is the bug that started all of this.
        chunks_before_delegation.extend(bridge.texts(EVT_TEXT_CHUNK))
        return {
            "status": "success",
            "operator": "Systems Operator",
            "response": "web01 has 4 updates; no reboot required.",
            "tool_calls": [],
        }

    monkeypatch.setattr("vigilus.api.chat.execute_delegation", _fake_delegation)

    await _run_orchestrator(
        [LLMMessage(role="user", content="check my servers for updates")],
        StreamingProvider([REPLY, "All servers checked — web01 has 4 updates."]),
        "system",
        db=db_session,
        session_id="sess-stream",
        bridge=bridge,
    )

    streamed = "".join(chunks_before_delegation)
    assert PLAN.startswith(streamed.strip())
    assert "Systems Operator" in streamed  # the useful part arrived in time


async def test_delegation_json_is_never_streamed_to_the_user(db_session, monkeypatch):
    bridge = RecordingBridge()

    async def _fake_delegation(*args, **kwargs):
        return {
            "status": "success",
            "operator": "Systems Operator",
            "response": "done",
            "tool_calls": [],
        }

    monkeypatch.setattr("vigilus.api.chat.execute_delegation", _fake_delegation)

    await _run_orchestrator(
        [LLMMessage(role="user", content="check my servers")],
        StreamingProvider([REPLY, "All done."]),
        "system",
        db=db_session,
        session_id="sess-stream",
        bridge=bridge,
    )

    everything_shown = "".join(bridge.texts(EVT_TEXT_CHUNK) + bridge.texts(EVT_TEXT_DELTA))
    assert "delegate" not in everything_shown
    assert "```" not in everything_shown


async def test_final_text_delta_still_closes_each_message(db_session, monkeypatch):
    """Chunks are a preview; the stripped text_delta remains authoritative."""
    bridge = RecordingBridge()

    async def _fake_delegation(*args, **kwargs):
        return {"status": "success", "operator": "Systems Operator", "response": "ok"}

    monkeypatch.setattr("vigilus.api.chat.execute_delegation", _fake_delegation)

    await _run_orchestrator(
        [LLMMessage(role="user", content="check my servers")],
        StreamingProvider([REPLY, "All done."]),
        "system",
        db=db_session,
        session_id="sess-stream",
        bridge=bridge,
    )

    deltas = bridge.texts(EVT_TEXT_DELTA)
    assert deltas == [PLAN, "All done."]
