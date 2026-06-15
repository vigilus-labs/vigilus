"""Live (in-flight) task API — view and cancel running orchestrator turns.

Distinct from /schedules (cron jobs). These are orchestrator turns currently
executing in this backend process. Cancellation is cooperative: the orchestrator
and operator loops stop at the next iteration boundary.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from vigilus.core.tasks import get_task_registry

router = APIRouter(prefix="/running-tasks", tags=["Running Tasks"])


class RunningTaskResponse(BaseModel):
    id: str
    session_id: str
    title: str
    started_at: str
    elapsed_seconds: float
    current_step: str
    operator: str | None = None
    cancelling: bool = False


@router.get("", response_model=list[RunningTaskResponse])
async def list_running_tasks():
    """List orchestrator turns currently running in this process."""
    return get_task_registry().list_running()


@router.get("/{session_id}")
async def get_running_task(session_id: str):
    """Get the running turn for *session_id* with its buffered activity.

    Returns ``{"running": false, "activity": []}`` when nothing is running —
    used by the chat to restore live state after navigating back to the page.
    """
    task = get_task_registry().get(session_id)
    if not task:
        return {"running": False, "activity": []}
    return {"running": True, **task.to_detail()}


@router.post("/{session_id}/cancel")
async def cancel_running_task(session_id: str):
    """Request cancellation of the turn running for *session_id*.

    Returns ok=True if a running turn was found and signalled. The turn stops
    at the next step boundary (it will not start new tool calls or delegations).
    """
    cancelled = get_task_registry().cancel(session_id)
    if not cancelled:
        raise HTTPException(
            status_code=404,
            detail="No running task for that session (it may have already finished).",
        )
    return {"ok": True, "session_id": session_id}
