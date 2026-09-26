"""Curated provider presets for the guided /login flow.

Both the web ProviderWizard and the TUI provider wizard render this list, so
the guided setup is identical everywhere. Entries map onto the existing
ProviderType values — the wizard ultimately just calls POST /api/providers.
"""

from __future__ import annotations

# Fallback model for Anthropic providers with no model configured. Also the
# preset the setup wizard offers, so the two can't drift apart.
ANTHROPIC_DEFAULT_MODEL = "claude-opus-4-8"

PROVIDER_CATALOG: list[dict] = [
    {
        "id": "anthropic",
        "label": "Anthropic",
        "type": "anthropic",
        "needs_api_key": True,
        "needs_base_url": False,
        "base_url": None,
        "key_url": "https://console.anthropic.com/settings/keys",
        "default_model": ANTHROPIC_DEFAULT_MODEL,
        "context_window": 200_000,
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
        "context_window": 128_000,
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
        "context_window": None,
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
        "context_window": 1_000_000,
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
        "context_window": 8_192,
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
        "context_window": None,
    },
]
