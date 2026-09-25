"""Actions API router."""

import csv
import io
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from vigilus.db.base import get_db
from vigilus.db.models import Action
from vigilus.schemas.action import ActionResponse

router = APIRouter(prefix="/actions", tags=["Actions"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]


def _action_response(action: Action, *, include_output: bool) -> ActionResponse:
    """Build a response without touching a deferred output column.

    The list query defers ``output`` so a page of docker logs does not ride
    along with every row. Reading the attribute there would lazy-load it.
    """
    return ActionResponse(
        id=action.id,
        event=action.event,
        actor=action.actor,
        operator_id=action.operator_id,
        tool_id=action.tool_id,
        tool_name=action.tool_name,
        server_id=action.server_id,
        args=action.args,
        outcome=action.outcome,
        error=action.error,
        output=action.output if include_output else None,
        duration_ms=action.duration_ms,
        session_id=action.session_id,
        created_at=action.created_at,
    )


@router.get("", response_model=list[ActionResponse])
async def list_actions(
    db: SessionDep,
    event: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
    server_id: str | None = None,
    tool_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[ActionResponse]:
    """List actions with optional filtering.

    Tool output is omitted here. ``GET /actions/{id}`` returns it.
    """
    stmt = select(Action).options(defer(Action.output)).order_by(Action.created_at.desc())

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
    return [_action_response(action, include_output=False) for action in result.scalars().all()]


@router.get("/export")
async def export_actions(db: SessionDep) -> StreamingResponse:
    """Export all actions as CSV."""
    stmt = select(Action).order_by(Action.created_at.desc())
    result = await db.execute(stmt)
    actions = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(
        [
            "ID",
            "Event",
            "Actor",
            "Operator_ID",
            "Tool_Name",
            "Outcome",
            "Error",
            "Duration_MS",
            "Created_At",
        ]
    )

    for action in actions:
        writer.writerow(
            [
                action.id,
                action.event,
                action.actor,
                action.operator_id or "",
                action.tool_name or "",
                action.outcome.value if action.outcome else "",
                action.error or "",
                action.duration_ms or "",
                action.created_at.isoformat() if action.created_at else "",
            ]
        )

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=actions_export.csv"},
    )


@router.get("/{action_id}", response_model=ActionResponse)
async def get_action(action_id: str, db: SessionDep) -> ActionResponse:
    """Get a specific action by ID."""
    action = await db.get(Action, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return _action_response(action, include_output=True)
