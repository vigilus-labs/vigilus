"""Memories API — CRUD for the persistent agent memory store.

Scopes: "global" (shared environment knowledge), "orchestrator"
(Vigilus-private), or an operator ID (operator-private).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.db.base import get_db
from vigilus.db.models import Memory
from vigilus.schemas.memory import MemoryCreate, MemoryResponse, MemoryUpdate

router = APIRouter(prefix="/memories", tags=["Memories"])


@router.get("", response_model=list[MemoryResponse])
async def list_memories(scope: str | None = None, db: AsyncSession = Depends(get_db)):
    """List memories, optionally filtered by scope (comma-separated for several)."""
    query = select(Memory).order_by(Memory.created_at.desc())
    if scope:
        scopes = [s.strip() for s in scope.split(",") if s.strip()]
        query = query.where(Memory.scope.in_(scopes))
    result = await db.execute(query)
    return [MemoryResponse.model_validate(m) for m in result.scalars().all()]


@router.post("", response_model=MemoryResponse)
async def create_memory(data: MemoryCreate, db: AsyncSession = Depends(get_db)):
    from vigilus.core.memory import save_memory

    memory = await save_memory(
        db,
        scope=data.scope,
        content=data.content,
        category=data.category,
        source="user",
    )
    await db.commit()
    await db.refresh(memory)
    return MemoryResponse.model_validate(memory)


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(memory_id: str, data: MemoryUpdate, db: AsyncSession = Depends(get_db)):
    memory = await db.get(Memory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    if data.content is not None:
        memory.content = data.content
    if data.category is not None:
        memory.category = data.category or None
    await db.commit()
    await db.refresh(memory)
    return MemoryResponse.model_validate(memory)


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, db: AsyncSession = Depends(get_db)):
    memory = await db.get(Memory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    await db.delete(memory)
    await db.commit()
    return {"ok": True}
