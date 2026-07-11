"""Tests for the three-tier system prompt builder."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.prompt_builder import (
    DEFAULT_DELEGATION_FORMAT,
    DEFAULT_IDENTITY,
    PromptBuilder,
    SystemPrompt,
)
from vigilus.db.models import Operator, Server


@pytest.mark.asyncio
async def test_system_prompt_render_empty():
    """Empty tiers produce an empty string."""
    prompt = SystemPrompt()
    assert prompt.render() == ""


@pytest.mark.asyncio
async def test_system_prompt_render_single_tier():
    """Only one tier populated."""
    prompt = SystemPrompt(stable="Hello", context="", volatile="")
    assert prompt.render() == "Hello"


@pytest.mark.asyncio
async def test_system_prompt_render_all_tiers():
    """All three tiers joined with double newlines."""
    prompt = SystemPrompt(stable="A", context="B", volatile="C")
    assert prompt.render() == "A\n\nB\n\nC"


@pytest.mark.asyncio
async def test_system_prompt_skips_empty_tiers():
    """Empty tiers are omitted."""
    prompt = SystemPrompt(stable="A", context="", volatile="C")
    assert prompt.render() == "A\n\nC"


@pytest.mark.asyncio
async def test_builder_stable_has_identity(db_session: AsyncSession):
    """Stable tier contains the default identity text."""
    builder = PromptBuilder(db=db_session)
    prompt = await builder.build()
    assert DEFAULT_IDENTITY.split(".")[0] in prompt.stable
    assert DEFAULT_DELEGATION_FORMAT.split(".")[0] in prompt.stable


@pytest.mark.asyncio
async def test_builder_stable_custom_identity(db_session: AsyncSession):
    """Custom identity replaces the default."""
    builder = PromptBuilder(db=db_session, custom_identity="I am custom Vigilus.")
    prompt = await builder.build()
    assert "I am custom Vigilus." in prompt.stable
    # Default identity should NOT be present
    assert "You are Vigilus, the primary security orchestrator" not in prompt.stable


@pytest.mark.asyncio
async def test_builder_includes_operator_roster(db_session: AsyncSession):
    """Stable tier includes enabled operators."""
    # Create an operator (no provider needed for roster)
    op = Operator(
        name="Test Monitor",
        description="A test security monitor",
        enabled=True,
    )
    db_session.add(op)
    await db_session.commit()

    builder = PromptBuilder(db=db_session)
    prompt = await builder.build()
    assert "Test Monitor" in prompt.stable
    assert "A test security monitor" in prompt.stable


@pytest.mark.asyncio
async def test_builder_excludes_disabled_operators(db_session: AsyncSession):
    """Disabled operators are not in the roster."""
    op = Operator(
        name="Disabled Op",
        description="Should not appear",
        enabled=False,
    )
    db_session.add(op)
    await db_session.commit()

    builder = PromptBuilder(db=db_session)
    prompt = await builder.build()
    assert "Disabled Op" not in prompt.stable


@pytest.mark.asyncio
async def test_builder_context_server_inventory(db_session: AsyncSession):
    """Context tier includes server inventory."""
    srv = Server(name="web-01", hostname="10.0.0.1", port=22, status="online")
    db_session.add(srv)
    await db_session.commit()

    builder = PromptBuilder(db=db_session)
    prompt = await builder.build()
    assert "web-01" in prompt.context
    assert "10.0.0.1" in prompt.context


@pytest.mark.asyncio
async def test_builder_context_empty_without_servers(db_session: AsyncSession):
    """Context tier is empty when no servers exist."""
    builder = PromptBuilder(db=db_session)
    prompt = await builder.build()
    assert prompt.context == ""


@pytest.mark.asyncio
async def test_builder_volatile_has_timestamp(db_session: AsyncSession):
    """Volatile tier always has a date."""
    builder = PromptBuilder(db=db_session)
    prompt = await builder.build()
    assert "Current date:" in prompt.volatile


@pytest.mark.asyncio
async def test_builder_vatile_memory_context(db_session: AsyncSession):
    """Volatile tier includes memory context when provided."""
    builder = PromptBuilder(db=db_session)
    prompt = await builder.build(memory_context="Found CVE-2024-1234 on server web-01")
    assert "CVE-2024-1234" in prompt.volatile
    assert "<memory-context>" in prompt.volatile


@pytest.mark.asyncio
async def test_rebuild_volatile_preserves_stable_and_context(db_session: AsyncSession):
    """rebuild_volatile keeps stable/context unchanged."""
    builder = PromptBuilder(db=db_session)
    original = await builder.build()
    rebuilt = await builder.rebuild_volatile(original, memory_context="new memory")

    assert rebuilt.stable == original.stable
    assert rebuilt.context == original.context
    assert "new memory" in rebuilt.volatile
    # Timestamp should also be present
    assert "Current date:" in rebuilt.volatile


@pytest.mark.asyncio
async def test_full_render_contains_all_parts(db_session: AsyncSession):
    """Full rendered prompt contains identity, delegation format, and timestamp."""
    builder = PromptBuilder(db=db_session)
    prompt = await builder.build()
    full = prompt.render()

    assert "You are Vigilus" in full
    assert "delegate" in full.lower()
    assert "Current date:" in full
