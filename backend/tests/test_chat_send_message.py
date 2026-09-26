"""POST /api/sessions/{id}/messages end to end, with a scripted LLM.

Pins the web chat turn's behaviour — persistence, @mention routing, task
title, SSE ``done`` payload, and the unconfigured-provider error — so moving
the turn onto the shared core path can't silently change it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from vigilus.api import chat as chat_api
from vigilus.core import orchestrator as orch
from vigilus.core.sse import get_bridge
from vigilus.core.tasks import get_task_registry
from vigilus.db.models import Message, MessageRole, Operator, Provider, ProviderType
from vigilus.db.models import Session as ChatSession
from vigilus.providers.base import AgentLLM, LLMResponse


class ScriptedProvider(AgentLLM):
    """Returns canned replies in order and records each call's system prompt."""

    def __init__(self, replies: list[str], on_call=None):
        self.replies = list(replies)
        self.systems: list[str] = []
        self.on_call = on_call
        self.default_model = "scripted-model"

    async def complete(self, messages, *, system=None, tools=None, temperature=0.0, **kwargs):
        self.systems.append(system or "")
        if self.on_call is not None:
            self.on_call()
        return LLMResponse(content=self.replies.pop(0))

    async def test_connection(self) -> bool:
        return True


@pytest.fixture
def scripted(monkeypatch):
    """Make the orchestrator resolve to a ScriptedProvider.

    Uses default orchestrator config (no provider_id → falls back to the
    default enabled Provider row) without touching data/orchestrator.json.
    """
    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())

    def _install(replies: list[str], on_call=None) -> ScriptedProvider:
        provider = ScriptedProvider(replies, on_call=on_call)
        monkeypatch.setattr("vigilus.providers.registry.build_provider", lambda row: provider)
        return provider

    return _install


@pytest.fixture
def recorded_bridges(monkeypatch):
    """Record every event published on bridges that send_message creates."""
    bridges: list = []

    class RecordingBridge(chat_api.StreamBridge):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.events: list[tuple[str, dict]] = []
            bridges.append(self)

        def publish(self, event: str, data: dict | None = None) -> None:
            self.events.append((event, data or {}))
            super().publish(event, data)

    monkeypatch.setattr(chat_api, "StreamBridge", RecordingBridge)
    return bridges


async def _default_provider(db) -> Provider:
    row = Provider(
        name="scripted",
        type=ProviderType.openai_compat,
        base_url="http://scripted.invalid",
        default_model="scripted-model",
        is_default=True,
        enabled=True,
    )
    db.add(row)
    await db.commit()
    return row


async def _new_session(client) -> str:
    res = await client.post("/api/sessions", json={})
    assert res.status_code == 200
    return res.json()["id"]


async def _messages(db, session_id: str) -> list[Message]:
    rows = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    )
    return list(rows.scalars().all())


async def test_plain_reply_is_persisted_and_returned(
    db_session, async_client, scripted, recorded_bridges
):
    await _default_provider(db_session)
    scripted(["Disk usage is fine on every server."])
    sid = await _new_session(async_client)

    res = await async_client.post(
        f"/api/sessions/{sid}/messages", json={"content": "check disk usage"}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "assistant"
    assert body["content"] == "Disk usage is fine on every server."

    rows = await _messages(db_session, sid)
    assert [(m.role, m.content) for m in rows] == [
        (MessageRole.user, "check disk usage"),
        (MessageRole.assistant, "Disk usage is fine on every server."),
    ]
    session = await db_session.get(ChatSession, sid)
    assert session.title == "check disk usage"

    # The SSE stream is told which message closed the turn.
    [bridge] = recorded_bridges
    assert ("done", {"session_id": sid, "message_id": body["id"]}) in bridge.events

    # The turn is released.
    assert get_task_registry().get(sid) is None
    assert get_bridge(sid) is None


async def test_mentioned_operator_is_pinned_in_the_system_prompt(
    db_session, async_client, scripted
):
    await _default_provider(db_session)
    db_session.add(
        Operator(name="Infra Ops", description="d", system_prompt="p", enabled=True, delegatable=True)
    )
    await db_session.commit()
    provider = scripted(["On it."])
    sid = await _new_session(async_client)

    res = await async_client.post(
        f"/api/sessions/{sid}/messages", json={"content": "@Infra Ops check disk usage"}
    )

    assert res.status_code == 200
    assert "## Explicit operator selection" in provider.systems[0]
    assert '"Infra Ops"' in provider.systems[0]


async def test_no_mention_means_no_operator_pinning(db_session, async_client, scripted):
    await _default_provider(db_session)
    provider = scripted(["Hello!"])
    sid = await _new_session(async_client)

    res = await async_client.post(f"/api/sessions/{sid}/messages", json={"content": "hi"})

    assert res.status_code == 200
    assert "Explicit operator selection" not in provider.systems[0]


async def test_running_task_is_titled_from_the_first_message(
    db_session, async_client, scripted
):
    await _default_provider(db_session)
    sid = await _new_session(async_client)
    titles: list[str] = []
    scripted(["Done."], on_call=lambda: titles.append(get_task_registry().get(sid).title))

    res = await async_client.post(
        f"/api/sessions/{sid}/messages", json={"content": "restart nginx on web-1\nthanks"}
    )

    assert res.status_code == 200
    assert titles == ["restart nginx on web-1"]


async def test_unconfigured_orchestrator_returns_500_and_saves_nothing(
    db_session, async_client, monkeypatch
):
    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())
    sid = await _new_session(async_client)  # no Provider rows exist

    res = await async_client.post(f"/api/sessions/{sid}/messages", json={"content": "hello"})

    assert res.status_code == 500
    assert "No provider configured" in res.json()["detail"]
    assert await _messages(db_session, sid) == []
    assert get_task_registry().get(sid) is None


async def test_mention_instruction_survives_compression(
    db_session, async_client, scripted, monkeypatch
):
    from vigilus.core.compressor import ContextCompressor

    async def _compressed(self, messages, *, system_tokens=0):
        return messages, "Earlier we patched nginx on web-1."

    monkeypatch.setattr(ContextCompressor, "compress_if_needed", _compressed)
    await _default_provider(db_session)
    db_session.add(
        Operator(name="Infra Ops", description="d", system_prompt="p", enabled=True, delegatable=True)
    )
    await db_session.commit()
    provider = scripted(["On it."])
    sid = await _new_session(async_client)

    res = await async_client.post(
        f"/api/sessions/{sid}/messages", json={"content": "@Infra Ops check disk usage"}
    )

    assert res.status_code == 200
    assert "Earlier we patched nginx on web-1." in provider.systems[0]
    assert "## Explicit operator selection" in provider.systems[0]


async def test_failed_turn_returns_500_and_frees_the_session(
    db_session, async_client, scripted, recorded_bridges, monkeypatch
):
    from vigilus.core.compressor import ContextCompressor

    real_compress = ContextCompressor.compress_if_needed
    calls = 0

    async def _explode_once(self, messages, *, system_tokens=0):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("compressor exploded")
        return await real_compress(self, messages, system_tokens=system_tokens)

    monkeypatch.setattr(ContextCompressor, "compress_if_needed", _explode_once)
    await _default_provider(db_session)
    scripted(["Recovered."])  # the first turn fails before reaching the LLM
    sid = await _new_session(async_client)

    res = await async_client.post(f"/api/sessions/{sid}/messages", json={"content": "hello"})

    assert res.status_code == 500
    assert res.json()["detail"] == "compressor exploded"
    assert get_task_registry().get(sid) is None
    assert get_bridge(sid) is None
    [bridge] = recorded_bridges
    assert ("error", {"error": "compressor exploded"}) in bridge.events
    assert ("done", {"session_id": sid}) in bridge.events

    # The session isn't stuck: the next message is accepted (not a 409).
    res = await async_client.post(f"/api/sessions/{sid}/messages", json={"content": "again"})
    assert res.status_code == 200
    assert res.json()["content"] == "Recovered."
