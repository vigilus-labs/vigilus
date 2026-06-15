"""Pydantic v2 schemas for the channels admin API.

Bot tokens are write-only: they appear in request models but are never
returned in responses (only a ``has_token`` / ``bot_username`` hint is).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChannelConfigUpsert(BaseModel):
    """Upsert body for PUT /channels/{platform}. Token is optional on update."""

    bot_token: str | None = Field(
        default=None,
        description="New bot token (encrypted at rest). Omit on update to keep current.",
    )
    bot_username: str | None = None
    enabled: bool = True
    respond_in_groups: bool = False
    default_operator_id: str | None = None


class ChannelConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    platform: str
    bot_username: str | None
    enabled: bool
    respond_in_groups: bool
    default_operator_id: str | None
    has_token: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChannelAccountUpsert(BaseModel):
    platform: str = Field(..., description="telegram | discord")
    external_user_id: str = Field(..., max_length=64)
    allowed: bool = False
    label: str | None = None
    user_id: str | None = None


class ChannelAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    platform: str
    external_user_id: str
    allowed: bool
    label: str | None
    user_id: str | None
    created_at: datetime


class ChannelTestResponse(BaseModel):
    ok: bool
    bot_username: str | None = None
    error: str | None = None
