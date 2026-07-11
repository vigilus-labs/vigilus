"""Pydantic v2 schemas for McpServer CRUD operations."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vigilus.core.command import parse_command_argv
from vigilus.db.models import McpServerStatus, McpTransport

# A github_url is later handed to `git clone`. git treats certain URL forms as
# code, not data: the `transport::address` helper syntax (e.g. `ext::sh -c ...`)
# executes arbitrary commands, `file://` reads local paths, and a value
# starting with `-` is parsed as a git option (argument injection). We allowlist
# only http(s)/git/ssh transports and the scp-like `user@host:path` form.
_GIT_SAFE_SCHEME = re.compile(r"^(?:https?|git|ssh)://", re.IGNORECASE)
_GIT_SCP_LIKE = re.compile(r"^(?:[A-Za-z0-9._~-]+@)?[A-Za-z0-9._-]+:(?!//)")


def validate_install_command(command: str | None) -> str | None:
    """Validate a single executable and argv-style install command."""
    if command is None:
        return None
    command = command.strip()
    if not command:
        return None
    parse_command_argv(command, field_name="install_command")
    return command


def validate_github_url(url: str | None) -> str | None:
    """Normalize and constrain a clone URL; raise ValueError if unsafe.

    Returns the stripped URL, or None for empty/None input.
    """
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith("-"):
        raise ValueError("github_url must not start with '-'")
    if "::" in url:
        # git's transport::address syntax (ext::, fd::, …) can run commands.
        raise ValueError("github_url must not contain '::'")
    if url.lower().startswith("file://"):
        raise ValueError("local file:// URLs are not allowed for github_url")
    if _GIT_SAFE_SCHEME.match(url) or _GIT_SCP_LIKE.match(url):
        return url
    raise ValueError(
        "github_url must be an http(s), git, or ssh URL "
        "(e.g. https://github.com/owner/repo.git)"
    )


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

    _check_github_url = field_validator("github_url")(
        lambda cls, v: validate_github_url(v)
    )
    _check_install_command = field_validator("install_command")(
        lambda cls, v: validate_install_command(v)
    )


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

    _check_github_url = field_validator("github_url")(
        lambda cls, v: validate_github_url(v)
    )
    _check_install_command = field_validator("install_command")(
        lambda cls, v: validate_install_command(v)
    )


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
