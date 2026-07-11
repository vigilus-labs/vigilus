"""Pydantic v2 schemas for Operator CRUD operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vigilus.db.models import PermissionLevel, TrustMode


class OperatorCreate(BaseModel):
    """Schema for creating a new operator."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    system_prompt: str | None = None
    soul: str | None = None
    provider_id: str | None = None
    model: str | None = None
    permission_level: PermissionLevel = PermissionLevel.read
    trust_mode: TrustMode = TrustMode.inherit
    working_dir: str | None = None
    is_builtin: bool = False
    delegatable: bool = True
    enabled: bool = True
    icon: str | None = None
    tool_ids: list[str] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def _description_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("description must not be empty or whitespace-only")
        return v


class OperatorUpdate(BaseModel):
    """Schema for updating an existing operator."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    system_prompt: str | None = None
    soul: str | None = None
    provider_id: str | None = None
    model: str | None = None
    permission_level: PermissionLevel | None = None
    trust_mode: TrustMode | None = None
    working_dir: str | None = None
    delegatable: bool | None = None
    enabled: bool | None = None
    icon: str | None = None
    tool_ids: list[str] | None = None

    @field_validator("description")
    @classmethod
    def _description_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("description must not be empty or whitespace-only")
        return v


class OperatorResponse(BaseModel):
    """Schema for operator API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    system_prompt: str | None = None
    soul: str | None = None
    provider_id: str | None = None
    model: str | None = None
    permission_level: PermissionLevel
    trust_mode: TrustMode
    working_dir: str | None = None
    is_builtin: bool = False
    delegatable: bool = True
    enabled: bool = True
    icon: str | None = None
    tool_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, operator) -> OperatorResponse:
        """Build response from an ORM Operator, including tool IDs."""
        tool_ids = [ot.tool_id for ot in (operator.operator_tools or [])]
        return cls(
            id=operator.id,
            name=operator.name,
            description=operator.description,
            system_prompt=operator.system_prompt,
            soul=operator.soul,
            provider_id=operator.provider_id,
            model=operator.model,
            permission_level=operator.permission_level,
            trust_mode=operator.trust_mode,
            working_dir=operator.working_dir,
            is_builtin=operator.is_builtin,
            delegatable=operator.delegatable,
            enabled=operator.enabled,
            icon=operator.icon,
            tool_ids=tool_ids,
            created_at=operator.created_at,
            updated_at=operator.updated_at,
        )
