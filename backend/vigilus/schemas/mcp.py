"""Pydantic v2 schemas for McpServer CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from vigilus.db.models import McpServerStatus, McpTransport


class McpServerCreate(BaseModel):
    """Schema for creating a new MCP server entry."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=255)
    command: str = Field(..., min_length=1)
    args: list[str] = Field(default_factory=list)
    env_vars: dict[str, Any] = Field(default_factory=dict)
    transport: Literal["stdio", "sse"] = "stdio"
    sse_url: Optional[str] = None
    autostart: bool = False
    github_url: Optional[str] = None
    install_command: Optional[str] = None
    working_dir: Optional[str] = None


class McpServerUpdate(BaseModel):
    """Schema for updating an existing MCP server."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    command: str | None = None
    args: list[str] | None = None
    env_vars: dict[str, Any] | None = None
    transport: Optional[Literal["stdio", "sse"]] = None
    sse_url: Optional[str] = None
    autostart: bool | None = None
    github_url: Optional[str] = None
    install_command: Optional[str] = None
    working_dir: Optional[str] = None


class McpServerResponse(BaseModel):
    """Schema for MCP server API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env_vars: dict[str, Any] = Field(default_factory=dict)
    transport: str
    sse_url: Optional[str] = None
    status: str = McpServerStatus.stopped
    autostart: bool = False
    github_url: Optional[str] = None
    install_command: Optional[str] = None
    working_dir: Optional[str] = None
    last_started_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
