"""Pydantic v2 schemas for audit Action records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vigilus.db.models import ActionOutcome


class ActionResponse(BaseModel):
    """Schema for action API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    event: str
    actor: str
    operator_id: str | None = None
    tool_id: str | None = None
    tool_name: str | None = None
    server_id: str | None = None
    args: dict[str, Any] | None = None
    outcome: ActionOutcome
    error: str | None = None
    duration_ms: float | None = None
    session_id: str | None = None
    created_at: datetime


class ActionQuery(BaseModel):
    """Schema for filtering / querying actions."""

    model_config = ConfigDict(from_attributes=True)

    event: str | None = None
    actor: str | None = None
    operator_id: str | None = None
    tool_id: str | None = None
    server_id: str | None = None
    session_id: str | None = None
    outcome: ActionOutcome | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
