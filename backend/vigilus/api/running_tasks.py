"""Live (in-flight) task API — view and cancel running orchestrator turns.

Distinct from /schedules (cron jobs). These are orchestrator turns currently
executing in this backend process. Cancellation is cooperative: the orchestrator
and operator loops stop at the next iteration boundary.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.tasks import get_task_registry
from vigilus.db.base import get_db
from vigilus.db.models import LlmUsage

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
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


async def _usage_by_session(db: AsyncSession, session_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Aggregate llm_usage rows for the given sessions → live token counters.

    One grouped query per request. Rows are written by ``record_llm_usage``
    after every provider call, so the counters advance while a turn runs.
    """
    if not session_ids:
        return {}
    rows = await db.execute(
        select(
            LlmUsage.session_id,
            func.sum(LlmUsage.input_tokens),
            func.sum(LlmUsage.output_tokens),
            func.sum(LlmUsage.estimated_cost_usd),
        )
        .where(LlmUsage.session_id.in_(session_ids))
        .group_by(LlmUsage.session_id)
    )
    return {
        sid: {
            "tokens_in": int(tokens_in or 0),
            "tokens_out": int(tokens_out or 0),
            "cost_usd": float(cost) if cost is not None else None,
        }
        for sid, tokens_in, tokens_out, cost in rows.all()
    }


@router.get("", response_model=list[RunningTaskResponse])
async def list_running_tasks(db: AsyncSession = Depends(get_db)):
    """List orchestrator turns currently running in this process."""
    tasks = get_task_registry().list_running()
    usage = await _usage_by_session(db, [t["session_id"] for t in tasks])
    for t in tasks:
        u = usage.get(t["session_id"], {})
        t.setdefault("tokens_in", u.get("tokens_in", 0))
        t.setdefault("tokens_out", u.get("tokens_out", 0))
        t.setdefault("cost_usd", u.get("cost_usd"))
    return tasks


@router.get("/{session_id}")
async def get_running_task(session_id: str, db: AsyncSession = Depends(get_db)):
    """Get the running turn for *session_id* with its buffered activity.

    Returns ``{"running": false, "activity": []}`` when nothing is running —
    used by the chat to restore live state after navigating back to the page.
    """
    task = get_task_registry().get(session_id)
    if not task:
        return {"running": False, "activity": []}
    detail = task.to_detail()
    usage = await _usage_by_session(db, [session_id])
    u = usage.get(session_id, {})
    detail.setdefault("tokens_in", u.get("tokens_in", 0))
    detail.setdefault("tokens_out", u.get("tokens_out", 0))
    detail.setdefault("cost_usd", u.get("cost_usd"))
    return {"running": True, **detail}


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
