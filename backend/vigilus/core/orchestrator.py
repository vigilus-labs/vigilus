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
import structlog
from dataclasses import dataclass, field, asdict
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vigilus.config import get_settings

logger = structlog.get_logger(__name__)

# Kept for backward compat / migration — the prompt_builder uses its own default.
DEFAULT_IDENTITY = """\
You are Vigilus, the primary security orchestrator for an IT operations platform.

Your ONLY role is to receive user requests and delegate them to specialist \
operators who have the actual tools to complete the work. You cannot run tools \
yourself — you coordinate the operators who can.
"""


@dataclass
class OrchestratorConfig:
    """Runtime-mutable config for the Vigilus orchestrator."""

    provider_id: str | None = None
    model: str | None = None
    # custom_identity overrides only the stable identity block.
    # If empty/None the prompt_builder's DEFAULT_IDENTITY is used.
    custom_identity: str | None = None
    # soul is a persona blurb appended to the stable identity block.
    soul: str | None = None

    # IANA timezone name (e.g. "America/New_York") used to interpret cron
    # schedules and to display run times. Defaults to UTC.
    timezone: str = "UTC"

    # Keep system_prompt as a read-only convenience for the API endpoint
    # (returns the rendered prompt).  Not persisted — rebuilt by PromptBuilder.
    system_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "custom_identity": self.custom_identity,
            "soul": self.soul,
            "timezone": self.timezone,
            "system_prompt": self.system_prompt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrchestratorConfig:
        return cls(
            provider_id=data.get("provider_id"),
            model=data.get("model"),
            custom_identity=data.get("custom_identity"),
            soul=data.get("soul"),
            timezone=data.get("timezone") or "UTC",
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
            with open(path, "r") as f:
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
            select(Provider).where(Provider.is_default == True, Provider.enabled == True)  # noqa: E712
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
        raise OrchestratorNotConfigured(
            "Orchestrator provider is disabled. Please enable it."
        )

    provider = build_provider(provider_row)
    model = orch_cfg.model or provider_row.default_model
    if hasattr(provider, "default_model") and model:
        provider.default_model = model

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
