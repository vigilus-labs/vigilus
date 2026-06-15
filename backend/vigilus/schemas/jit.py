"""Pydantic v2 schemas for JIT (Just-In-Time) access requests."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from vigilus.db.models import JitPermission, JitStatus


class JitRequestResponse(BaseModel):
    """Schema for JIT request API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    operator_id: str
    resource: str
    permission: JitPermission
    task_description: str
    status: JitStatus = JitStatus.pending
    token_id: str | None = None
    ttl_minutes: int
    scope_mode: str = "timed"
    requested_at: datetime
    resolved_at: datetime | None = None
    approved_by: str | None = None
    created_at: datetime


class JitApproveRequest(BaseModel):
    """Schema for approving a JIT request with a chosen grant scope.

    - ``single_use`` → authorize only the triggering command ("once").
    - ``ttl_minutes`` → how long a reusable ("timed") grant lasts; server clamps
      to ``jit_max_ttl_minutes``. Ignored when ``single_use`` is true.
    - ``resource`` → optionally broaden ("*") or narrow the covered resource.
    """

    model_config = ConfigDict(from_attributes=True)

    approved: bool = True
    approved_by: str = Field(default="admin", min_length=1)
    ttl_minutes: int | None = None
    single_use: bool = False
    resource: str | None = None
