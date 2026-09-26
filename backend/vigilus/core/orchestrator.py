"""Vigilus orchestrator configuration and runtime.

Vigilus is the top-level orchestrator — NOT an Operator.
It lives on the /chat page, has its own provider/model/system_prompt
configurable at runtime, and its only job is to delegate tasks to
specialist Operators.

The system prompt is built by `core/prompt_builder.py` in three tiers:
stable (identity + roster), context (servers), volatile (memory + time).
The `custom_identity` field here overrides only the stable identity block.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from vigilus.config import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class OrchestratorConfig:
    """Runtime-mutable config for the Vigilus orchestrator."""

    provider_id: str | None = None
    model: str | None = None
    # Optional cheaper model for the orchestrator's routing calls, on the same
    # provider. Unset means ``model`` (current behavior).
    router_model: str | None = None
    # Optional provider and model for conversation compression. Unset provider
    # means the caller’s provider; unset model means a cheap default for known
    # hosted providers, otherwise that provider’s own default.
    summarizer_provider_id: str | None = None
    summarizer_model: str | None = None
    # custom_identity overrides only the stable identity block.
    # If empty/None the prompt_builder's DEFAULT_IDENTITY is used.
    custom_identity: str | None = None
    # soul is a persona blurb appended to the stable identity block.
    soul: str | None = None

    # IANA timezone name (e.g. "America/New_York") used to interpret cron
    # schedules and to display run times. Defaults to UTC.
    timezone: str = "UTC"

    # Platform-wide monthly LLM spend cap in USD. None/0 = no budget. When the
    # month's metered spend reaches this, new LLM calls are stopped until the
    # budget is raised or the month resets (core/budget.py).
    monthly_budget_usd: float | None = None

    # Keep system_prompt as a read-only convenience for the API endpoint
    # (returns the rendered prompt).  Not persisted — rebuilt by PromptBuilder.
    system_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "router_model": self.router_model,
            "summarizer_provider_id": self.summarizer_provider_id,
            "summarizer_model": self.summarizer_model,
            "custom_identity": self.custom_identity,
            "soul": self.soul,
            "timezone": self.timezone,
            "monthly_budget_usd": self.monthly_budget_usd,
            "system_prompt": self.system_prompt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrchestratorConfig:
        budget = data.get("monthly_budget_usd")
        try:
            budget = float(budget) if budget is not None else None
        except (TypeError, ValueError):
            budget = None
        if budget is not None and budget <= 0:
            budget = None
        return cls(
            provider_id=data.get("provider_id"),
            model=data.get("model"),
            router_model=data.get("router_model") or None,
            summarizer_provider_id=data.get("summarizer_provider_id") or None,
            summarizer_model=data.get("summarizer_model") or None,
            custom_identity=data.get("custom_identity"),
            soul=data.get("soul"),
            timezone=data.get("timezone") or "UTC",
            monthly_budget_usd=budget,
            system_prompt=data.get("system_prompt", ""),
        )


_config_cache: OrchestratorConfig | None = None


def _config_path() -> str:
    settings = get_settings()
    return os.path.join(settings.data_dir, "orchestrator.json")


def load_orchestrator_config() -> OrchestratorConfig:
    """Load orchestrator config from disk, or return defaults."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                _config_cache = OrchestratorConfig.from_dict(json.load(f))
            return _config_cache
        except Exception as e:
            logger.warning("orchestrator.config_load_failed", error=str(e))

    _config_cache = OrchestratorConfig()
    return _config_cache


def get_app_timezone() -> ZoneInfo:
    """Return the configured app timezone as a ZoneInfo.

    Falls back to UTC (fail-safe) if the stored value is missing or not a
    valid IANA zone, logging a warning so the misconfiguration is visible.
    """
    name = load_orchestrator_config().timezone or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        logger.warning("orchestrator.bad_timezone", timezone=name, error=str(e))
        return ZoneInfo("UTC")


class OrchestratorNotConfigured(Exception):
    """Raised when no usable LLM provider exists for the orchestrator."""


async def resolve_orchestrator_provider(db):
    """Resolve the orchestrator's LLM provider and model from config + DB.

    Falls back to the default enabled provider when the orchestrator has
    none assigned. Returns (provider_instance, provider_row, model).
    Raises OrchestratorNotConfigured with a user-friendly message otherwise.
    """
    from sqlalchemy import select

    from vigilus.db.models import Provider
    from vigilus.providers.registry import build_provider

    orch_cfg = load_orchestrator_config()
    provider_id = orch_cfg.provider_id

    if not provider_id:
        result = await db.execute(
            select(Provider).where(
                Provider.is_default.is_(True), Provider.enabled.is_(True)
            )  # noqa: E712
        )
        fallback = result.scalar_one_or_none()
        if not fallback:
            raise OrchestratorNotConfigured(
                "No provider configured for Vigilus. Go to the Settings page "
                "and assign a provider to the orchestrator."
            )
        provider_id = fallback.id

    provider_row = await db.get(Provider, provider_id)
    if not provider_row:
        raise OrchestratorNotConfigured(
            "Orchestrator provider not found. Please reconfigure it in Settings."
        )
    if not provider_row.enabled:
        raise OrchestratorNotConfigured("Orchestrator provider is disabled. Please enable it.")

    provider = build_provider(provider_row)
    model = orch_cfg.model or provider_row.default_model
    if hasattr(provider, "default_model") and model:
        provider.default_model = model

    return provider, provider_row, model


def resolve_loop_model(configured_model: str | None) -> str | None:
    """Model the orchestrator loop should call.

    ``router_model`` overrides the orchestrator model when set. Both use the
    orchestrator's provider.
    """
    return load_orchestrator_config().router_model or configured_model


# Cheap/fast defaults for compression when the user has not picked a model.
# Local and custom providers are absent on purpose: their model ids are not
# ours to guess.
CHEAP_SUMMARY_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.5-flash",
    "openrouter": "google/gemini-2.5-flash",
}


def _provider_type_name(provider_row) -> str:
    provider_type = provider_row.type
    return provider_type.value if hasattr(provider_type, "value") else str(provider_type)


async def resolve_summarizer(db, *, fallback_provider_row, fallback_model: str | None):
    """Provider and model for a compression call.

    Returns ``(provider_instance, provider_row, model)``. A missing or disabled
    summarizer provider falls back to ``fallback_provider_row`` so a bad
    setting cannot stop the turn.
    """
    from vigilus.db.models import Provider
    from vigilus.providers.registry import build_provider

    cfg = load_orchestrator_config()
    provider_row = fallback_provider_row
    if cfg.summarizer_provider_id:
        row = await db.get(Provider, cfg.summarizer_provider_id)
        if row is not None and row.enabled:
            provider_row = row

    provider = build_provider(provider_row)
    provider_type = _provider_type_name(provider_row)
    if cfg.summarizer_model:
        model = cfg.summarizer_model
    elif provider_type in CHEAP_SUMMARY_MODELS:
        model = CHEAP_SUMMARY_MODELS[provider_type]
    else:
        model = provider_row.default_model or fallback_model
    return provider, provider_row, model


def save_orchestrator_config(config: OrchestratorConfig) -> None:
    """Persist orchestrator config to disk."""
    global _config_cache
    settings = get_settings()
    os.makedirs(settings.data_dir, exist_ok=True)
    with open(_config_path(), "w") as f:
        json.dump(config.to_dict(), f, indent=2)
    _config_cache = config
    logger.info("orchestrator.config_saved", provider_id=config.provider_id, model=config.model)
