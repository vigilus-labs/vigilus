"""core.turn: the single orchestrator-turn implementation."""

from __future__ import annotations

import pytest_asyncio

from vigilus.core import orchestrator as orch
from vigilus.core.orchestrator import resolve_loop_model, resolve_summarizer
from vigilus.core.prompt_builder import SystemPrompt
from vigilus.core.turn import execute_turn, run_turn, system_parts, turn_title
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


def test_system_parts_keep_server_status_out_of_the_cache_prefix():
    prompt = SystemPrompt(
        stable="You are Vigilus.",
        context="## Server inventory\n\n- 🟢 **nas**",
        volatile="Current date: Saturday",
    )
    cached, volatile = system_parts(prompt, "Mentioned operator.")
    assert cached == "You are Vigilus."
    assert volatile is not None
    assert "🟢" not in cached
    assert "🟢" in volatile
    assert "Current date" in volatile
    assert volatile.endswith("Mentioned operator.")


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


def test_router_model_overrides_the_orchestrator_model(monkeypatch):
    monkeypatch.setattr(
        orch, "_config_cache", orch.OrchestratorConfig(model="claude-opus-4-8", router_model="claude-haiku-4-5")
    )
    assert resolve_loop_model("claude-opus-4-8") == "claude-haiku-4-5"
    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig(model="claude-opus-4-8"))
    assert resolve_loop_model("claude-opus-4-8") == "claude-opus-4-8"


async def test_summarizer_defaults_and_can_use_another_provider(db_session, monkeypatch):
    anthropic = Provider(
        name="anth",
        type=ProviderType.anthropic,
        default_model="claude-opus-4-8",
        enabled=True,
    )
    local = Provider(
        name="local",
        type=ProviderType.openai_compat,
        base_url="http://localhost:11434/v1",
        default_model="llama3",
        enabled=True,
    )
    openai = Provider(
        name="oa",
        type=ProviderType.openai,
        default_model="gpt-4o",
        enabled=True,
    )
    db_session.add_all([anthropic, local, openai])
    await db_session.commit()

    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())
    _, row, model = await resolve_summarizer(
        db_session, fallback_provider_row=anthropic, fallback_model="claude-opus-4-8"
    )
    assert row.id == anthropic.id
    assert model == "claude-haiku-4-5"

    _, row, model = await resolve_summarizer(
        db_session, fallback_provider_row=local, fallback_model="claude-opus-4-8"
    )
    assert row.id == local.id
    assert model == "llama3"

    monkeypatch.setattr(
        orch,
        "_config_cache",
        orch.OrchestratorConfig(summarizer_provider_id=openai.id, summarizer_model="gpt-4o-mini"),
    )
    _, row, model = await resolve_summarizer(
        db_session, fallback_provider_row=anthropic, fallback_model="claude-opus-4-8"
    )
    assert row.id == openai.id
    assert model == "gpt-4o-mini"


async def test_run_turn_still_returns_text(db_session, chat):
    session, script = chat
    script(["Plain text."])

    assert await run_turn(db_session, session, "hi") == "Plain text."
