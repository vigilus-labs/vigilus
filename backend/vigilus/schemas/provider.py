"""Pydantic v2 schemas for Provider CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vigilus.db.models import ProviderType


class ProviderCreate(BaseModel):
    """Schema for creating a new provider."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=255)
    type: ProviderType
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    extra_headers: dict[str, Any] | None = None
    tool_calling_supported: bool = True
    enabled: bool = True
    is_default: bool = False


class ProviderUpdate(BaseModel):
    """Schema for updating an existing provider."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    type: ProviderType | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    extra_headers: dict[str, Any] | None = None
    tool_calling_supported: bool | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ProviderResponse(BaseModel):
    """Schema for provider API responses – api_key is never returned."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: ProviderType
    base_url: str | None = None
    has_api_key: bool = False
    default_model: str | None = None
    extra_headers: dict[str, Any] | None = None
    tool_calling_supported: bool = True
    enabled: bool = True
    is_default: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, provider) -> "ProviderResponse":
        """Build response from an ORM Provider, masking the api_key."""
        return cls(
            id=provider.id,
            name=provider.name,
            type=provider.type,
            base_url=provider.base_url,
            has_api_key=bool(provider.api_key),
            default_model=provider.default_model,
            extra_headers=provider.extra_headers,
            tool_calling_supported=provider.tool_calling_supported,
            enabled=provider.enabled,
            created_at=provider.created_at,
            updated_at=provider.updated_at,
        )
