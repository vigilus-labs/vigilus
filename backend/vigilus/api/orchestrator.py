"""Orchestrator config API – provider, model, system prompt for Vigilus."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.orchestrator import (
    load_orchestrator_config,
    save_orchestrator_config,
)
from vigilus.db.base import get_db
from vigilus.db.models import Provider

router = APIRouter(prefix="/orchestrator", tags=["Orchestrator"])


class OrchestratorConfigResponse(BaseModel):
    provider_id: str | None = None
    model: str | None = None
    system_prompt: str
    custom_identity: str | None = None
    soul: str | None = None
    timezone: str = "UTC"


class OrchestratorConfigUpdate(BaseModel):
    provider_id: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    custom_identity: str | None = None
    soul: str | None = None
    timezone: str | None = None


@router.get("", response_model=OrchestratorConfigResponse)
async def get_orchestrator_config():
    """Get the current Vigilus orchestrator configuration."""
    cfg = load_orchestrator_config()
    return OrchestratorConfigResponse(**cfg.to_dict())


@router.patch("", response_model=OrchestratorConfigResponse)
async def update_orchestrator_config(
    data: OrchestratorConfigUpdate, db: AsyncSession = Depends(get_db)
):
    """Update the Vigilus orchestrator configuration.

    If provider_id is changed, verify it exists.
    """
    cfg = load_orchestrator_config()

    if data.provider_id is not None:
        if data.provider_id == "":
            cfg.provider_id = None
        else:
            provider = await db.get(Provider, data.provider_id)
            if not provider:
                raise HTTPException(status_code=400, detail="Provider not found")
            cfg.provider_id = data.provider_id

    if data.model is not None:
        cfg.model = data.model if data.model else None

    if data.custom_identity is not None:
        cfg.custom_identity = data.custom_identity if data.custom_identity else None

    if data.soul is not None:
        cfg.soul = data.soul if data.soul else None

    tz_changed = False
    if data.timezone is not None:
        tz = data.timezone or "UTC"
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(status_code=422, detail=f"Invalid timezone: {tz}")
        tz_changed = tz != cfg.timezone
        cfg.timezone = tz

    # system_prompt is now built dynamically — accept but store as custom_identity
    # if the user explicitly sets it via the old API field.
    if data.system_prompt is not None:
        # If it's different from the default, treat it as a custom identity override
        from vigilus.core.prompt_builder import DEFAULT_IDENTITY

        if data.system_prompt and data.system_prompt != DEFAULT_IDENTITY:
            cfg.custom_identity = data.system_prompt

    save_orchestrator_config(cfg)

    # A timezone change re-interprets every cron schedule, so refresh the
    # running scheduler's jobs and recompute next-run times.
    if tz_changed:
        from vigilus.core.scheduler import get_scheduler

        await get_scheduler().reschedule_all()

    return OrchestratorConfigResponse(**cfg.to_dict())
