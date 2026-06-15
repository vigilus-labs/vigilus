"""Scheduled tasks API – CRUD + manual run for recurring orchestrator tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.scheduler import (
    execute_scheduled_task,
    get_scheduler,
    next_fire_time,
    validate_cron,
)
from vigilus.db.base import get_db
from vigilus.db.models import Operator, ScheduledTask

router = APIRouter(prefix="/schedules", tags=["Schedules"])
logger = structlog.get_logger(__name__)


# ── Schemas ────────────────────────────────────────────────


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    cron_expression: str = Field(min_length=1, max_length=128)
    task_prompt: str = Field(min_length=1)
    operator_id: str | None = None
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    cron_expression: str | None = None
    task_prompt: str | None = None
    operator_id: str | None = None
    enabled: bool | None = None


class ScheduleResponse(BaseModel):
    id: str
    name: str
    description: str | None
    cron_expression: str
    task_prompt: str
    operator_id: str | None
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_status: str | None
    last_result: dict | None
    run_count: int
    created_at: datetime
    updated_at: datetime


def _to_response(task: ScheduledTask) -> ScheduleResponse:
    return ScheduleResponse(
        id=task.id,
        name=task.name,
        description=task.description,
        cron_expression=task.cron_expression,
        task_prompt=task.task_prompt,
        operator_id=task.operator_id,
        enabled=task.enabled,
        last_run_at=task.last_run_at,
        next_run_at=task.next_run_at,
        last_status=task.last_status,
        last_result=task.last_result,
        run_count=task.run_count or 0,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


async def _check_cron(expression: str) -> None:
    err = validate_cron(expression)
    if err:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid cron expression '{expression}': {err}. "
            "Use 5 fields: minute hour day month day-of-week "
            "(e.g. '0 8 * * *' for daily 8 AM UTC).",
        )


async def _check_operator(db: AsyncSession, operator_id: str | None) -> None:
    if operator_id:
        op = await db.get(Operator, operator_id)
        if not op:
            raise HTTPException(status_code=400, detail="Operator not found")


# ── Endpoints ──────────────────────────────────────────────


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ScheduledTask).order_by(ScheduledTask.created_at))
    return [_to_response(t) for t in result.scalars().all()]


@router.post("", response_model=ScheduleResponse, status_code=201)
async def create_schedule(data: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    await _check_cron(data.cron_expression)
    await _check_operator(db, data.operator_id)

    existing = await db.execute(select(ScheduledTask).where(ScheduledTask.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"A task named '{data.name}' already exists")

    task = ScheduledTask(
        name=data.name,
        description=data.description,
        cron_expression=data.cron_expression,
        task_prompt=data.task_prompt,
        operator_id=data.operator_id,
        enabled=data.enabled,
        next_run_at=next_fire_time(data.cron_expression) if data.enabled else None,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    get_scheduler().sync_task(task)
    logger.info("schedule.created", name=task.name, cron=task.cron_expression)
    return _to_response(task)


@router.get("/{task_id}", response_model=ScheduleResponse)
async def get_schedule(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return _to_response(task)


@router.patch("/{task_id}", response_model=ScheduleResponse)
async def update_schedule(task_id: str, data: ScheduleUpdate, db: AsyncSession = Depends(get_db)):
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    if data.cron_expression is not None:
        await _check_cron(data.cron_expression)
        task.cron_expression = data.cron_expression
    if data.operator_id is not None:
        if data.operator_id == "":
            task.operator_id = None
        else:
            await _check_operator(db, data.operator_id)
            task.operator_id = data.operator_id
    if data.name is not None:
        task.name = data.name
    if data.description is not None:
        task.description = data.description
    if data.task_prompt is not None:
        task.task_prompt = data.task_prompt
    if data.enabled is not None:
        task.enabled = data.enabled

    task.next_run_at = next_fire_time(task.cron_expression) if task.enabled else None
    await db.commit()
    await db.refresh(task)

    get_scheduler().sync_task(task)
    logger.info("schedule.updated", name=task.name, enabled=task.enabled)
    return _to_response(task)


@router.delete("/{task_id}")
async def delete_schedule(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    get_scheduler().remove_task(task.id)
    await db.delete(task)
    await db.commit()
    return {"ok": True}


@router.post("/{task_id}/run", response_model=ScheduleResponse)
async def run_schedule_now(task_id: str, db: AsyncSession = Depends(get_db)):
    """Trigger a manual run immediately (in the background)."""
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    if task.last_status == "running":
        raise HTTPException(status_code=409, detail="Task is already running")

    task.last_status = "running"
    await db.commit()
    await db.refresh(task)

    asyncio.create_task(execute_scheduled_task(task.id, force=True))
    logger.info("schedule.manual_run", name=task.name)
    return _to_response(task)
