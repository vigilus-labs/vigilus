from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from vigilus.db.base import get_db
from vigilus.db.models import JitRequest, Operator, Provider
from vigilus.schemas.jit import JitRequestResponse, JitApproveRequest
from vigilus.core.rbac import WardenService

router = APIRouter(prefix="/jit", tags=["JIT"])

def _to_response(req: JitRequest) -> JitRequestResponse:
    op_name = req.operator.name if req.operator else "Unknown"
    return JitRequestResponse(
        id=req.id,
        operator_id=req.operator_id,
        operator_name=op_name,
        resource=req.resource,
        permission=req.permission.value,
        task_description=req.task_description,
        status=req.status.value,
        token_id=req.token_id,
        ttl_minutes=req.ttl_minutes,
        scope_mode=req.scope_mode,
        requested_at=req.requested_at,
        resolved_at=req.resolved_at,
        approved_by=req.approved_by,
        created_at=req.created_at
    )

@router.get("", response_model=list[JitRequestResponse])
async def list_jit_requests(status: str | None = None, db: AsyncSession = Depends(get_db)):
    query = select(JitRequest).options(selectinload(JitRequest.operator)).order_by(JitRequest.requested_at.desc())
    if status:
        query = query.where(JitRequest.status == status)
    result = await db.execute(query)
    return [_to_response(r) for r in result.scalars().all()]

@router.get("/{request_id}", response_model=JitRequestResponse)
async def get_jit_request(request_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(JitRequest).options(selectinload(JitRequest.operator)).where(JitRequest.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="JIT Request not found")
    return _to_response(req)

@router.post("/{request_id}/approve", response_model=JitRequestResponse)
async def approve_jit_request(
    request_id: str,
    body: JitApproveRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    warden = WardenService()
    body = body or JitApproveRequest()
    try:
        await warden.approve_request(
            db,
            request_id,
            approver=body.approved_by or "admin_ui",
            ttl_minutes=body.ttl_minutes,
            single_use=body.single_use,
            resource=body.resource,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    result = await db.execute(select(JitRequest).options(selectinload(JitRequest.operator)).where(JitRequest.id == request_id))
    req = result.scalar_one()
    return _to_response(req)

@router.post("/{request_id}/deny", response_model=JitRequestResponse)
async def deny_jit_request(request_id: str, db: AsyncSession = Depends(get_db)):
    warden = WardenService()
    try:
        await warden.deny_request(db, request_id, approver="admin_ui")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    result = await db.execute(select(JitRequest).options(selectinload(JitRequest.operator)).where(JitRequest.id == request_id))
    req = result.scalar_one()
    return _to_response(req)
