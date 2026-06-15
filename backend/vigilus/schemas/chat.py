"""Pydantic v2 schemas for chat sessions and messages."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vigilus.db.models import MessageRole


class SessionCreate(BaseModel):
    """Schema for creating a new chat session."""

    model_config = ConfigDict(from_attributes=True)

    title: str | None = None
    operator_context: str | None = None
    operator_id: str | None = None


class SessionUpdate(BaseModel):
    """Schema for updating an existing chat session."""

    model_config = ConfigDict(from_attributes=True)

    title: str | None = None
    operator_id: str | None = None


class SessionResponse(BaseModel):
    """Schema for session API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None = None
    operator_context: str | None = None
    operator_id: str | None = None
    origin: str | None = None  # web | telegram | discord | schedule
    created_at: datetime
    last_active_at: datetime
    message_count: int = 0


class MessageCreate(BaseModel):
    """Schema for creating a new message within a session."""

    model_config = ConfigDict(from_attributes=True)

    role: MessageRole = MessageRole.user
    content: str | dict[str, Any] | list[Any] = Field(...)
    operator_id: str | None = None


class MessageResponse(BaseModel):
    """Schema for message API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: MessageRole
    content: str | dict[str, Any] | list[Any]
    operator_id: str | None = None
    created_at: datetime
