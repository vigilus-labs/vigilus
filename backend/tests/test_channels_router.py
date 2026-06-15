"""Tests for the channel router policy core (allowlist, gating, dispatch).

Uses the global session factory (wired to the in-memory test DB by the
db_session fixture) and a fake adapter. ``run_turn`` is monkeypatched so no
LLM provider is needed.
"""

from __future__ import annotations

import pytest

from vigilus.db.base import get_session_factory
from vigilus.db.models import ChannelAccount, ChannelChat, Session
from vigilus.integrations.base import InboundMessage
from vigilus.integrations import router as router_mod


class FakeAdapter:
    """Captures outbound sends; status/typing are no-ops."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))

    async def send_typing(self, chat_id: str) -> None:
        return None

    async def send_status(self, chat_id: str, text: str):
        return None

    async def edit_status(self, chat_id: str, handle, text: str) -> None:
        return None

    async def delete_status(self, chat_id: str, handle) -> None:
        return None

    async def send_jit_prompt(self, chat_id: str, text: str, request_id: str) -> None:
        await self.send(chat_id, text)


async def _seed_account(platform: str, external_user_id: str, *, allowed: bool, label=None):
    factory = get_session_factory()
    async with factory() as db:
        db.add(ChannelAccount(
            platform=platform,
            external_user_id=external_user_id,
            allowed=allowed,
            label=label,
        ))
        await db.commit()


def _inbound(platform="telegram", *, chat_id="c1", user_id="u1", text="hi",
             is_group=False, mentioned=False):
    return InboundMessage(
        platform=platform, chat_id=chat_id, user_id=user_id,
        user_name="tester", text=text, is_group=is_group, mentioned=mentioned,
    )


@pytest.mark.asyncio
async def test_deny_path_replies_in_dm(db_session):
    await _seed_account("telegram", "u1", allowed=False)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(), adapter)
    assert len(adapter.sent) == 1
    assert "not authorized" in adapter.sent[0][1].lower()


@pytest.mark.asyncio
async def test_deny_path_silent_in_group(db_session):
    await _seed_account("telegram", "u1", allowed=False)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(is_group=True), adapter)
    assert adapter.sent == []  # no reply in groups


@pytest.mark.asyncio
async def test_allowed_user_group_without_mention_is_gated(db_session, monkeypatch):
    await _seed_account("telegram", "u1", allowed=True)
    called = {"n": 0}

    async def _fake_run_turn(db, session, text, *, bridge=None, **kw):
        called["n"] += 1
        return "ok"

    monkeypatch.setattr(router_mod, "run_turn", _fake_run_turn)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(is_group=True, mentioned=False), adapter)
    assert called["n"] == 0
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_allowed_user_group_with_mention_runs(db_session, monkeypatch):
    await _seed_account("telegram", "u1", allowed=True)
    called = {"n": 0}

    async def _fake_run_turn(db, session, text, *, bridge=None, **kw):
        called["n"] += 1
        return "the answer"

    monkeypatch.setattr(router_mod, "run_turn", _fake_run_turn)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(is_group=True, mentioned=True), adapter)
    assert called["n"] == 1
    assert adapter.sent[-1] == ("c1", "the answer")


@pytest.mark.asyncio
async def test_command_path_does_not_call_run_turn(db_session, monkeypatch):
    await _seed_account("telegram", "u1", allowed=True)

    async def _fail_run_turn(*a, **kw):
        raise AssertionError("run_turn should not be called for a command")

    monkeypatch.setattr(router_mod, "run_turn", _fail_run_turn)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(text="/help"), adapter)
    assert adapter.sent, "command should produce a reply"
    assert "command" in adapter.sent[-1][1].lower() or "|" in adapter.sent[-1][1]


@pytest.mark.asyncio
async def test_chat_path_creates_session_and_link(db_session, monkeypatch):
    await _seed_account("telegram", "u1", allowed=True, label="me")
    seen = {}

    async def _fake_run_turn(db, session, text, *, bridge=None, **kw):
        seen["session_id"] = session.id
        seen["origin"] = session.origin
        seen["text"] = text
        return "final reply"

    monkeypatch.setattr(router_mod, "run_turn", _fake_run_turn)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(text="hello world"), adapter)

    assert seen["text"] == "hello world"
    assert seen["origin"] == "telegram"
    assert adapter.sent[-1] == ("c1", "final reply")

    # Session + ChannelChat link persisted.
    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import select

        link = (await db.execute(
            select(ChannelChat).where(
                ChannelChat.platform == "telegram",
                ChannelChat.external_chat_id == "c1",
            )
        )).scalar_one()
        session = await db.get(Session, link.session_id)
        assert session is not None
        assert session.origin == "telegram"


@pytest.mark.asyncio
async def test_chat_path_session_continuity(db_session, monkeypatch):
    """A second message in the same chat reuses the same Session."""
    await _seed_account("telegram", "u1", allowed=True)
    sessions = []

    async def _fake_run_turn(db, session, text, *, bridge=None, **kw):
        sessions.append(session.id)
        return "ok"

    monkeypatch.setattr(router_mod, "run_turn", _fake_run_turn)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(chat_id="c1", text="first"), adapter)
    await router_mod.handle_inbound(_inbound(chat_id="c1", text="second"), adapter)
    assert sessions[0] == sessions[1]  # same session both times


@pytest.mark.asyncio
async def test_plan_is_relayed_before_final_reply(db_session, monkeypatch):
    """The orchestrator's pre-delegation plan reaches the channel as its own
    message, ahead of the final answer — and the final isn't double-posted."""
    from vigilus.api.sse import EVT_DELEGATION_START, EVT_TEXT_DELTA

    await _seed_account("telegram", "u1", allowed=True)

    async def _fake_run_turn(db, session, text, *, bridge=None, **kw):
        # Mirror the orchestrator loop: publish the plan prose, then delegate.
        bridge.publish(EVT_TEXT_DELTA, {"text": "I'll have the SOC Operator check alerts."})
        bridge.publish(EVT_DELEGATION_START, {"operator": "SOC Operator"})
        return "All clear — no critical alerts."

    monkeypatch.setattr(router_mod, "run_turn", _fake_run_turn)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(text="any alerts?"), adapter)

    bodies = [body for _chat, body in adapter.sent]
    assert "I'll have the SOC Operator check alerts." in bodies
    assert "All clear — no critical alerts." in bodies
    # Plan precedes the final reply.
    assert bodies.index("I'll have the SOC Operator check alerts.") < bodies.index(
        "All clear — no critical alerts."
    )
    # Final reply posted exactly once (no duplication via the text_delta path).
    assert bodies.count("All clear — no critical alerts.") == 1


@pytest.mark.asyncio
async def test_final_only_turn_is_not_double_posted(db_session, monkeypatch):
    """A turn with no delegation publishes its answer as text_delta too; the
    buffered prose must not be sent on top of the router's final reply."""
    from vigilus.api.sse import EVT_TEXT_DELTA

    await _seed_account("telegram", "u1", allowed=True)

    async def _fake_run_turn(db, session, text, *, bridge=None, **kw):
        bridge.publish(EVT_TEXT_DELTA, {"text": "Here is your answer."})
        return "Here is your answer."

    monkeypatch.setattr(router_mod, "run_turn", _fake_run_turn)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(text="hi"), adapter)

    bodies = [body for _chat, body in adapter.sent]
    assert bodies == ["Here is your answer."]


@pytest.mark.asyncio
async def test_orchestrator_not_configured(db_session, monkeypatch):
    from vigilus.core.orchestrator import OrchestratorNotConfigured

    await _seed_account("telegram", "u1", allowed=True)

    async def _raise(db, session, text, *, bridge=None, **kw):
        raise OrchestratorNotConfigured("none")

    monkeypatch.setattr(router_mod, "run_turn", _raise)
    adapter = FakeAdapter()
    await router_mod.handle_inbound(_inbound(text="hi"), adapter)
    assert "provider is configured" in adapter.sent[-1][1].lower()
