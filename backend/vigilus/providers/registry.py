"""Registry for building Provider instances."""

from __future__ import annotations

from cryptography.fernet import InvalidToken

from vigilus.core.crypto import decrypt
from vigilus.db.models import Provider, ProviderType
from vigilus.providers.base import AgentLLM
from vigilus.providers.anthropic_provider import AnthropicProvider
from vigilus.providers.openai_provider import OpenAIProvider
from vigilus.providers.openai_compat import OpenAICompatProvider
from vigilus.providers.google_provider import GoogleProvider

def build_provider(provider_row: Provider) -> AgentLLM:
    """Build a provider instance from a DB row."""
    api_key = None
    if provider_row.api_key:
        try:
            api_key = decrypt(provider_row.api_key)
        except InvalidToken:
            raise ValueError(
                f"The stored API key for '{provider_row.name}' cannot be decrypted — "
                "VIGILUS_SECRET has changed since the key was saved. "
                "Edit the provider and re-enter the API key."
            )
        
    if provider_row.type == ProviderType.anthropic:
        return AnthropicProvider(
            api_key=api_key or "sk-ant-dummy", 
            default_model=provider_row.default_model or "claude-3-5-sonnet-20241022"
        )
    elif provider_row.type == ProviderType.openai:
        return OpenAIProvider(
            api_key=api_key or "sk-dummy-key-required",
            default_model=provider_row.default_model or "gpt-4o"
        )
    elif provider_row.type == ProviderType.openrouter:
        return OpenAICompatProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_model=provider_row.default_model or "openrouter/auto",
            extra_headers={
                "HTTP-Referer": "https://vigilus.ai",
                "X-Title": "Vigilus",
                **(provider_row.extra_headers or {}),
            },
        )
    elif provider_row.type == ProviderType.google:
        return GoogleProvider(
            api_key=api_key or "no-key",
            default_model=provider_row.default_model or "gemini-1.5-pro",
        )
    elif provider_row.type in (ProviderType.openai_compat, ProviderType.custom):
        if not provider_row.base_url:
            raise ValueError(
                f"Provider '{provider_row.name}' needs a Base URL pointing at an "
                "OpenAI-compatible endpoint (e.g. http://localhost:11434/v1 for Ollama)."
            )
        return OpenAICompatProvider(
            base_url=provider_row.base_url,
            api_key=api_key,
            default_model=provider_row.default_model or "local-model",
            extra_headers=provider_row.extra_headers
        )
    else:
        raise ValueError(
            f"Provider type '{provider_row.type.value}' is not supported yet. "
            "Use anthropic, openai, openai_compat, or custom."
        )
