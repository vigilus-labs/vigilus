"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from vigilus import __version__
from vigilus.config import get_settings


class SpaStaticFiles(StaticFiles):
    """StaticFiles with history-API fallback for the built SPA.

    A hard refresh on a client-side route (e.g. /chat, /servers) reaches this
    mount with a path that has no matching file; plain StaticFiles turns that
    into a 404 ({"detail": "Not Found"}). Serve index.html instead so the React
    router can take over. API paths and asset-like paths (last segment contains
    a dot, e.g. stale hashed bundles) still 404 normally.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not self._should_fallback(path):
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and self._should_fallback(path):
            return await super().get_response("index.html", scope)
        return response

    @staticmethod
    def _should_fallback(path: str) -> bool:
        if path == "api" or path.startswith("api/"):
            return False
        return "." not in path.rsplit("/", 1)[-1]


def _configure_logging() -> None:
    """Set up structlog for the application."""
    settings = get_settings()
    log_level = structlog._log_levels.NAME_TO_LEVEL.get(
        settings.log_level.lower(), structlog._log_levels.INFO
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan – startup and shutdown hooks."""
    from vigilus.db.base import close_db, get_session_factory, init_db
    from vigilus.db.seed import run_seed

    logger = structlog.get_logger("vigilus.lifespan")

    # ── Startup ─────────────────────────────────────────────
    settings = get_settings()

    # Ensure data directory exists
    os.makedirs(settings.data_dir, exist_ok=True)

    logger.info("startup.init_db")
    await init_db()

    logger.info("startup.seed")
    factory = get_session_factory()
    async with factory() as session:
        await run_seed(session)

    # Warn (non-blocking) if the DB is stamped behind the latest migration.
    # create_all (above) adds missing tables but never adds columns to existing
    # tables, so a schema-drifted DB fails at query time with a cryptic
    # "no such column" — this turns that silent drift into a clear log line.
    try:
        from vigilus.core.preflight import check_migration_status

        mig = await check_migration_status()
        if not mig.up_to_date:
            logger.warning(
                "startup.migrations_behind",
                current=mig.current,
                head=",".join(mig.heads),
                detail=mig.detail,
            )
        else:
            logger.info("startup.migrations_ok", revision=mig.current or "unstamped")
    except Exception:  # noqa: BLE001 — advisory check must never block startup
        logger.exception("startup.migration_check_failed")

    # Any tool actions still 'pending' belong to a turn that died when the
    # process was last stopped — mark them interrupted so they don't look live.
    from sqlalchemy import update as _sql_update

    from vigilus.db.models import Action, ActionOutcome

    async with factory() as session:
        result = await session.execute(
            _sql_update(Action)
            .where(Action.outcome == ActionOutcome.pending)
            .values(outcome=ActionOutcome.error, error="Interrupted — backend restarted")
        )
        if result.rowcount:
            logger.info("startup.cleared_orphaned_actions", count=result.rowcount)
        await session.commit()

    # Start the cron scheduler for recurring tasks
    from vigilus.core.scheduler import get_scheduler

    logger.info("startup.scheduler")
    await get_scheduler().start()

    # Start the channel gateway (Telegram / Discord adapters)
    from vigilus.integrations.gateway import get_gateway

    logger.info("startup.gateway")
    await get_gateway().start()

    # Preflight: warn (non-blocking) if nmap privileged scans won't work.
    # Catches direct `uvicorn` usage where the operator won't see `vigilus doctor`.
    try:
        from vigilus.core.preflight import check_nmap_access
        nmap = check_nmap_access()
        if nmap.installed and not nmap.privileged_ok:
            logger.warning("startup.nmap_no_privileged", detail=nmap.detail)
        elif not nmap.installed:
            logger.info("startup.nmap_not_installed")
    except Exception:  # noqa: BLE001
        pass

    # Reconcile MCP server state, then autostart configured servers.
    # A previous process that died without cleaning up leaves DB rows marked
    # 'running' while the in-memory connections dict is empty — flip them to
    # 'stopped' so the UI matches reality before we decide what to start.
    logger.info("startup.mcp")
    from sqlalchemy import select as _sql_select

    from vigilus.db.models import McpServer, McpServerStatus
    from vigilus.mcp_host.manager import McpManager

    mcp_manager = McpManager()
    async with factory() as session:
        stale = await session.execute(
            _sql_select(McpServer).where(McpServer.status == McpServerStatus.running)
        )
        stale_servers = stale.scalars().all()
        for srv in stale_servers:
            srv.status = McpServerStatus.stopped
            srv.last_error = "Backend restarted — process was not running"
        if stale_servers:
            logger.info("startup.mcp_reconciled", count=len(stale_servers))
        await session.commit()

        autostart = await session.execute(
            _sql_select(McpServer).where(McpServer.autostart.is_(True))
        )
        autostart_servers = autostart.scalars().all()

    for srv in autostart_servers:
        try:
            logger.info("startup.mcp_autostart", server=srv.name)
            await mcp_manager.start_server(srv)
        except Exception:  # noqa: BLE001
            logger.exception("startup.mcp_autostart_failed", server=srv.name)

    logger.info("startup.complete", version=__version__)

    yield

    # ── Shutdown ────────────────────────────────────────────
    logger.info("shutdown.start")
    await get_gateway().shutdown()
    await get_scheduler().shutdown()

    # Stop every live MCP subprocess so stdio children don't outlive the
    # backend and leave DB status stale for the next start.
    try:
        from vigilus.mcp_host.manager import McpManager as _McpManager

        mcp_mgr = _McpManager()
        if mcp_mgr.connections:
            logger.info("shutdown.mcp", count=len(mcp_mgr.connections))
            for server_id in list(mcp_mgr.connections.keys()):
                try:
                    await mcp_mgr.stop_server(server_id)
                except Exception:  # noqa: BLE001
                    logger.exception("shutdown.mcp_stop_failed", server_id=server_id)
    except Exception:  # noqa: BLE001
        logger.exception("shutdown.mcp_failed")

    await close_db()
    logger.info("shutdown.complete")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    _configure_logging()
    settings = get_settings()

    app = FastAPI(
        title="Vigilus",
        description="AI-powered infrastructure management platform",
        version=__version__,
        lifespan=lifespan,
    )

    # ── CORS ────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routers ─────────────────────────────────────────
    from fastapi import Depends

    from vigilus.api.auth import router as auth_router
    from vigilus.api.deps import require_user
    from vigilus.api.system import router as system_router
    from vigilus.api.providers import router as providers_router
    from vigilus.api.tools import router as tools_router
    from vigilus.api.actions import router as actions_router
    from vigilus.api.operators import router as operators_router
    from vigilus.api.chat import router as chat_router
    from vigilus.api.jit import router as jit_router
    from vigilus.api.mcp import router as mcp_router
    from vigilus.api.servers import router as servers_router
    from vigilus.api.credentials import router as credentials_router
    from vigilus.api.orchestrator import router as orchestrator_router
    from vigilus.api.schedules import router as schedules_router
    from vigilus.api.memories import router as memories_router
    from vigilus.api.running_tasks import router as running_tasks_router
    from vigilus.api.commands import router as commands_router
    from vigilus.api.channels import router as channels_router
    from vigilus.api.scope import router as scope_router
    from vigilus.api.search import router as search_router

    auth_dep = [Depends(require_user)]

    app.include_router(auth_router, prefix="/api")                           # public + self-authed
    app.include_router(system_router, prefix="/api", dependencies=auth_dep)
    app.include_router(providers_router, prefix="/api", dependencies=auth_dep)
    app.include_router(tools_router, prefix="/api", dependencies=auth_dep)
    app.include_router(actions_router, prefix="/api", dependencies=auth_dep)
    app.include_router(operators_router, prefix="/api", dependencies=auth_dep)
    app.include_router(chat_router, prefix="/api", dependencies=auth_dep)
    app.include_router(chat_router)  # /ws at root — WebSocket auth handled inside the endpoint
    app.include_router(jit_router, prefix="/api", dependencies=auth_dep)
    app.include_router(orchestrator_router, prefix="/api", dependencies=auth_dep)
    app.include_router(schedules_router, prefix="/api", dependencies=auth_dep)
    app.include_router(memories_router, prefix="/api", dependencies=auth_dep)
    app.include_router(running_tasks_router, prefix="/api", dependencies=auth_dep)
    app.include_router(commands_router, prefix="/api", dependencies=auth_dep)
    app.include_router(channels_router, prefix="/api", dependencies=auth_dep)
    app.include_router(mcp_router, prefix="/api", dependencies=auth_dep)
    app.include_router(servers_router, prefix="/api", dependencies=auth_dep)
    app.include_router(credentials_router, prefix="/api", dependencies=auth_dep)
    app.include_router(scope_router, prefix="/api", dependencies=auth_dep)
    app.include_router(search_router, prefix="/api", dependencies=auth_dep)

    # ── Static files (production SPA) ───────────────────────
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
    if os.path.isdir(frontend_dir):
        app.mount("/", SpaStaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app

app = create_app()
