from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.llm_usage import get_usage_summary
from vigilus.db.base import get_db
from vigilus.schemas.usage import UsageSummaryResponse

router = APIRouter(prefix="/usage", tags=["Usage"])

Window = Literal["today", "7d", "30d", "all"]


@router.get("", response_model=UsageSummaryResponse)
async def get_usage(
    window: Window = Query("7d"),
    db: AsyncSession = Depends(get_db),
):
    data = await get_usage_summary(db, window)
    return UsageSummaryResponse.model_validate(data)
