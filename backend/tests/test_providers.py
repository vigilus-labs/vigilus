import pytest

from vigilus.db.models import Provider, ProviderType
from vigilus.providers.anthropic_provider import AnthropicProvider
from vigilus.providers.openai_compat import OpenAICompatProvider
from vigilus.providers.openai_provider import OpenAIProvider
from vigilus.providers.registry import build_provider


@pytest.mark.asyncio
async def test_build_provider_anthropic():
    p = Provider(
        type=ProviderType.anthropic,
        name="test-anthropic",
        api_key=None,  # Will be None since not encrypted in DB model directly here
        default_model="claude-3-opus",
    )
    agent = build_provider(p)
    assert isinstance(agent, AnthropicProvider)
    assert agent.default_model == "claude-3-opus"


@pytest.mark.asyncio
async def test_build_provider_openai():
    p = Provider(type=ProviderType.openai, name="test-openai", api_key=None, default_model="gpt-4o")
    agent = build_provider(p)
    assert isinstance(agent, OpenAIProvider)
    assert agent.default_model == "gpt-4o"


@pytest.mark.asyncio
async def test_build_provider_openai_compat():
    p = Provider(
        type=ProviderType.openai_compat,
        name="test-compat",
        base_url="http://localhost:11434/v1",
        default_model="llama3",
    )
    agent = build_provider(p)
    assert isinstance(agent, OpenAICompatProvider)
    assert agent.base_url == "http://localhost:11434/v1"
    assert agent.default_model == "llama3"
