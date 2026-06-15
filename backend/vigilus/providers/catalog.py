"""Curated provider presets for the guided /login flow.

Both the web ProviderWizard and the TUI provider wizard render this list, so
the guided setup is identical everywhere. Entries map onto the existing
ProviderType values — the wizard ultimately just calls POST /api/providers.
"""

from __future__ import annotations

PROVIDER_CATALOG: list[dict] = [
    {
        "id": "anthropic",
        "label": "Anthropic",
        "type": "anthropic",
        "needs_api_key": True,
        "needs_base_url": False,
        "base_url": None,
        "key_url": "https://console.anthropic.com/settings/keys",
        "default_model": "claude-opus-4-8",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "type": "openai",
        "needs_api_key": True,
        "needs_base_url": False,
        "base_url": None,
        "key_url": "https://platform.openai.com/api-keys",
        "default_model": "gpt-4o",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "type": "openrouter",
        "needs_api_key": True,
        "needs_base_url": False,
        "base_url": None,
        "key_url": "https://openrouter.ai/settings/keys",
        "default_model": "openrouter/auto",
    },
    {
        "id": "google",
        "label": "Google Gemini",
        "type": "google",
        "needs_api_key": True,
        "needs_base_url": False,
        "base_url": None,
        "key_url": "https://aistudio.google.com/apikey",
        "default_model": "gemini-2.5-pro",
    },
    {
        "id": "ollama",
        "label": "Ollama (local)",
        "type": "openai_compat",
        "needs_api_key": False,
        "needs_base_url": False,
        "base_url": "http://localhost:11434/v1",
        "key_url": None,
        "default_model": None,
    },
    {
        "id": "custom",
        "label": "Custom (OpenAI-compatible)",
        "type": "openai_compat",
        "needs_api_key": False,
        "needs_base_url": True,
        "base_url": None,
        "key_url": None,
        "default_model": None,
    },
]
