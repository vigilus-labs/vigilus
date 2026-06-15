"""Pydantic v2 schemas for Memory CRUD operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryCreate(BaseModel):
    """Schema for creating a memory (from the UI)."""

    model_config = ConfigDict(from_attributes=True)

    scope: str = Field("global", min_length=1, max_length=64)
    content: str = Field(..., min_length=1)
    category: str | None = Field(None, max_length=64)

    @field_validator("content")
    @classmethod
    def _content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be empty or whitespace-only")
        return v.strip()


class MemoryUpdate(BaseModel):
    """Schema for editing a memory."""

    model_config = ConfigDict(from_attributes=True)

    content: str | None = None
    category: str | None = None

    @field_validator("content")
    @classmethod
    def _content_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("content must not be empty or whitespace-only")
        return v.strip() if v else v


class MemoryResponse(BaseModel):
    """Schema for memory API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    content: str
    category: str | None = None
    source: str | None = None
    created_at: datetime
    updated_at: datetime
