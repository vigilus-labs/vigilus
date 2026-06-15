"""Actions API router."""

import csv
import io
from typing import Annotated, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.db.base import get_db
from vigilus.db.models import Action
from vigilus.schemas.action import ActionResponse, ActionQuery

router = APIRouter(prefix="/actions", tags=["Actions"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]

@router.get("", response_model=list[ActionResponse])
async def list_actions(
    db: SessionDep,
    event: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
    server_id: str | None = None,
    tool_name: str | None = None,
    limit: int = 50,
    offset: int = 0
) -> Sequence[Action]:
    """List actions with optional filtering."""
    stmt = select(Action).order_by(Action.created_at.desc())
    
    filters = []
    if event:
        filters.append(Action.event == event)
    if actor:
        filters.append(Action.actor == actor)
    if outcome:
        filters.append(Action.outcome == outcome)
    if server_id:
        filters.append(Action.server_id == server_id)
    if tool_name:
        filters.append(Action.tool_name == tool_name)
        
    if filters:
        stmt = stmt.where(and_(*filters))
        
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.get("/export")
async def export_actions(db: SessionDep) -> StreamingResponse:
    """Export all actions as CSV."""
    stmt = select(Action).order_by(Action.created_at.desc())
    result = await db.execute(stmt)
    actions = result.scalars().all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["ID", "Event", "Actor", "Operator_ID", "Tool_Name", "Outcome", "Error", "Duration_MS", "Created_At"])
    
    for action in actions:
        writer.writerow([
            action.id,
            action.event,
            action.actor,
            action.operator_id or "",
            action.tool_name or "",
            action.outcome.value if action.outcome else "",
            action.error or "",
            action.duration_ms or "",
            action.created_at.isoformat() if action.created_at else ""
        ])
        
    output.seek(0)
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=actions_export.csv"}
    )

@router.get("/{action_id}", response_model=ActionResponse)
async def get_action(action_id: str, db: SessionDep) -> Action:
    """Get a specific action by ID."""
    action = await db.get(Action, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return action

