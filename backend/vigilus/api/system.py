"""System and health-check API routes."""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus import __version__
from vigilus.config import get_settings
from vigilus.db.base import get_db

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check() -> dict:
    """Basic liveness probe."""
    return {"status": "ok", "version": __version__}


@router.get("/system/update")
async def update_status() -> dict:
    """Report whether a newer Vigilus release is available (cached)."""
    from vigilus.core.updates import get_update_status

    return await get_update_status()


@router.post("/system/update/check")
async def update_check() -> dict:
    """Force a fresh check against the release server, bypassing the cache."""
    from vigilus.core.updates import get_update_status

    return await get_update_status(force=True)


@router.get("/system/status")
async def system_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Extended readiness check including database connectivity."""
    settings = get_settings()

    # Check DB
    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    # Count MCP servers
    mcp_running = 0
    try:
        from sqlalchemy import func, select

        from vigilus.db.models import McpServer, McpServerStatus

        res = await db.execute(
            select(func.count())
            .select_from(McpServer)
            .where(McpServer.status == McpServerStatus.running)
        )
        mcp_running = res.scalar() or 0
    except Exception:
        pass

    return {
        "version": __version__,
        "db_ok": db_ok,
        "mcp_servers_running": mcp_running,
        "trust_mode": settings.default_trust_mode,
    }


@router.get("/system/metrics")
async def system_metrics(db: AsyncSession = Depends(get_db)) -> dict:
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from vigilus.db.models import Action, ActionOutcome, JitRequest, Operator

    # Active operators
    res = await db.execute(
        select(func.count()).select_from(Operator).where(Operator.enabled.is_(True))
    )
    active_operators = res.scalar() or 0

    # Pending JITs
    res = await db.execute(
        select(func.count()).select_from(JitRequest).where(JitRequest.status == "pending")
    )
    pending_jits = res.scalar() or 0

    # Failed Actions (last 24h)
    yesterday = datetime.now(UTC) - timedelta(days=1)
    res = await db.execute(
        select(func.count())
        .select_from(Action)
        .where((Action.outcome == ActionOutcome.error) | (Action.outcome == ActionOutcome.denied))
        .where(Action.created_at >= yesterday)
    )
    failed_actions = res.scalar() or 0

    # Total Actions
    res = await db.execute(select(func.count()).select_from(Action))
    total_actions = res.scalar() or 0

    return {
        "active_operators": active_operators,
        "pending_jits": pending_jits,
        "failed_actions_24h": failed_actions,
        "total_actions": total_actions,
    }
