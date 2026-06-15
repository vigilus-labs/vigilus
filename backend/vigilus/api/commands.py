"""API routes for the shared slash-command system."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.commands import execute_command, get_command_specs
from vigilus.db.base import get_db
from vigilus.schemas.command import CommandResult, CommandRunRequest, CommandSpec

router = APIRouter(prefix="/commands", tags=["Commands"])


@router.get("", response_model=list[CommandSpec])
async def list_commands():
    """List all slash commands (server- and client-executed) for autocomplete."""
    return get_command_specs()


@router.post("/run", response_model=CommandResult)
async def run_command(req: CommandRunRequest, db: AsyncSession = Depends(get_db)):
    """Execute a server-side slash command in the context of a session."""
    return await execute_command(req.command, req.args, req.session_id, db)
