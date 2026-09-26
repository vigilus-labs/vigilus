"""core.turn: the single orchestrator-turn implementation."""

from __future__ import annotations

import pytest_asyncio

from vigilus.core import orchestrator as orch
from vigilus.core.turn import execute_turn, run_turn, turn_title
from vigilus.db.models import MessageRole, Provider, ProviderType
from vigilus.db.models import Session as ChatSession
from vigilus.providers.base import AgentLLM, LLMResponse


class ScriptedProvider(AgentLLM):
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.default_model = "scripted-model"

    async def complete(self, messages, **kwargs) -> LLMResponse:
        return LLMResponse(content=self.replies.pop(0))

    async def test_connection(self) -> bool:
        return True


@pytest_asyncio.fixture
async def chat(db_session, monkeypatch):
    """A default provider, a fresh session, and a hook to script replies."""
    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())
    db_session.add(
        Provider(
            name="scripted",
            type=ProviderType.openai_compat,
            base_url="http://scripted.invalid",
            default_model="scripted-model",
            is_default=True,
            enabled=True,
        )
    )
    session = ChatSession(title="New Chat", origin="web")
    db_session.add(session)
    await db_session.commit()

    def _script(replies: list[str]) -> None:
        provider = ScriptedProvider(replies)
        monkeypatch.setattr("vigilus.providers.registry.build_provider", lambda row: provider)

    return session, _script


def _session(title):
    return ChatSession(title=title, origin="web")


def test_turn_title_uses_first_line_of_untitled_session():
    assert turn_title(_session("New Chat"), "restart nginx\nplease") == "restart nginx"
    assert turn_title(_session(None), "restart nginx") == "restart nginx"


def test_turn_title_truncates_long_first_lines():
    title = turn_title(_session("New Chat"), "x" * 80)
    assert title == "x" * 57 + "…"


def test_turn_title_keeps_existing_titles_and_blank_input():
    assert turn_title(_session("Weekly patching"), "anything") == "Weekly patching"
    assert turn_title(_session("New Chat"), "   ") == "New Chat"


async def test_execute_turn_returns_persisted_rows(db_session, chat):
    session, script = chat
    script(["All servers are up."])

    result = await execute_turn(db_session, session, "status?")

    assert result.text == "All servers are up."
    assert result.assistant_message is not None
    assert result.assistant_message.id is not None
    assert result.assistant_message.role == MessageRole.assistant
    assert result.user_message is not None
    assert result.user_message.content == "status?"
    assert session.title == "status?"


async def test_execute_turn_without_saving_user_message(db_session, chat):
    session, script = chat
    script(["Retried."])

    result = await execute_turn(db_session, session, "status?", save_user_message=False)

    assert result.user_message is None
    assert result.text == "Retried."


async def test_run_turn_still_returns_text(db_session, chat):
    session, script = chat
    script(["Plain text."])

    assert await run_turn(db_session, session, "hi") == "Plain text."
