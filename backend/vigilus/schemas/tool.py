"""Pydantic v2 schemas for Tool CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vigilus.db.models import PermissionLevel, ToolImplementationType


class ToolCreate(BaseModel):
    """Schema for creating a new tool."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    implementation_type: ToolImplementationType = ToolImplementationType.native
    required_permission: PermissionLevel = PermissionLevel.read
    native_handler: str | None = None
    http_config: dict[str, Any] | None = None
    mcp_server_id: str | None = None
    mcp_tool_name: str | None = None
    is_builtin: bool = False
    available: bool = True


class ToolUpdate(BaseModel):
    """Schema for updating an existing tool."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    implementation_type: ToolImplementationType | None = None
    required_permission: PermissionLevel | None = None
    native_handler: str | None = None
    http_config: dict[str, Any] | None = None
    mcp_server_id: str | None = None
    mcp_tool_name: str | None = None
    available: bool | None = None


class ToolResponse(BaseModel):
    """Schema for tool API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    implementation_type: ToolImplementationType
    required_permission: PermissionLevel
    native_handler: str | None = None
    http_config: dict[str, Any] | None = None
    mcp_server_id: str | None = None
    mcp_tool_name: str | None = None
    is_builtin: bool = False
    available: bool = True
    created_at: datetime
