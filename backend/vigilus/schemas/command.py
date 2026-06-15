"""Pydantic schemas for the shared slash-command system.

Commands are defined once on the backend (core/commands.py) and consumed by
both the web chat and the TUI, so the two clients always expose the same set.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class CommandArg(BaseModel):
    name: str
    required: bool = False
    description: str = ""


class CommandSpec(BaseModel):
    name: str                  # "model" → typed as /model
    summary: str               # one-line description, shown in autocomplete
    usage: str                 # e.g. "/model [model-name]"
    args: list[CommandArg] = []
    # "server" commands run via POST /api/commands/run; "client" commands are
    # declared here for unified autocomplete but handled inside each client
    # (e.g. /login opens a wizard UI).
    execution: Literal["server", "client"] = "server"
    needs_session: bool = False


class CommandRunRequest(BaseModel):
    command: str
    args: str = ""
    session_id: str | None = None


class CommandResult(BaseModel):
    kind: Literal[
        "markdown",
        "error",
        "session_created",
        "session_switch",
        "session_deleted",
        "config_changed",
        "stopped",
    ]
    text: str = ""             # markdown rendered as an ephemeral system notice
    data: dict[str, Any] | None = None
