"""Pydantic v2 schemas for Server CRUD operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from vigilus.db.models import ServerStatus


class ServerCreate(BaseModel):
    """Schema for creating a new server."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=255)
    hostname: str = Field(..., min_length=1)
    port: int = 22
    os: str | None = None
    os_version: str | None = None
    tags: list[str] = Field(default_factory=list)
    credential_id: str | None = None
    notes: str | None = None


class ServerUpdate(BaseModel):
    """Schema for updating an existing server."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    hostname: str | None = None
    port: int | None = None
    os: str | None = None
    os_version: str | None = None
    tags: list[str] | None = None
    credential_id: str | None = None
    notes: str | None = None
    status: ServerStatus | None = None


class ServerResponse(BaseModel):
    """Schema for server API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    hostname: str
    port: int = 22
    os: str | None = None
    os_version: str | None = None
    tags: list[str] = Field(default_factory=list)
    credential_id: str | None = None
    notes: str | None = None
    last_seen: datetime | None = None
    status: ServerStatus = ServerStatus.unknown
    created_at: datetime
    updated_at: datetime
