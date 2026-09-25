"""Tests for the three-tier system prompt builder."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from vigilus.core.operator_runtime import OperatorRuntime
from vigilus.core.prompt_builder import (
    DEFAULT_DELEGATION_FORMAT,
    DEFAULT_IDENTITY,
    PromptBuilder,
    SystemPrompt,
)
from vigilus.db.models import Memory, Operator, PermissionLevel, Provider, ProviderType, Server


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
async def test_builder_context_uses_latest_50_memories_in_stable_order(
    db_session: AsyncSession,
):
    """Prompt memory recall keeps the newest window and orders timestamp ties consistently."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    memories = [
        Memory(
            id=f"memory-{index:03d}",
            scope="global",
            content=f"memory-{index:03d}",
            created_at=start + timedelta(minutes=min(index, 55)),
        )
        for index in range(60)
    ]
    memories.extend(
        Memory(
            id=f"operator-memory-{index:03d}",
            scope="operator-1",
            content=f"operator-memory-{index:03d}",
            created_at=start + timedelta(days=1, minutes=min(index, 55)),
        )
        for index in range(60)
    )
    provider = Provider(
        id="provider-1",
        name="memory-test-provider",
        type=ProviderType.openrouter,
        default_model="test-model",
        enabled=True,
    )
    operator = Operator(
        id="operator-1",
        name="Memory test operator",
        description="Tests operator prompt memory recall",
        permission_level=PermissionLevel.read,
        system_prompt="You are a memory test operator.",
    )
    operator.provider = provider
    db_session.add_all([*memories, provider, operator])
    await db_session.commit()
    set_committed_value(operator, "provider", provider)
    set_committed_value(operator, "operator_tools", [])

    prompt = await PromptBuilder(db=db_session).build()

    recalled = [
        line.removeprefix("- ")
        for line in prompt.context.splitlines()
        if line.startswith("- memory-")
    ]
    assert recalled == [f"memory-{index:03d}" for index in range(10, 60)]
    assert "operator-memory-010" not in prompt.context

    operator_prompt = await OperatorRuntime(operator)._build_system_prompt([])
    assert operator_prompt is not None
    operator_recalled = [
        line.removeprefix("- ")
        for line in operator_prompt.splitlines()
        if line.startswith("- operator-memory-")
    ]
    assert operator_recalled == [f"operator-memory-{index:03d}" for index in range(10, 60)]
    assert not any(line.startswith("- memory-") for line in operator_prompt.splitlines())


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
