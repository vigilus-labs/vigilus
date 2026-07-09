"""Async SQLAlchemy engine, session factory, and base model."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from vigilus.config import get_settings

logger = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _build_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_async_engine(
        settings.database_url,
        echo=False,
        connect_args=connect_args,
    )


def _build_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


_engine = None
_async_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory():
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = _build_session_factory(get_engine())
    return _async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _stamp_alembic_head_if_unstamped(sync_connection) -> None:  # noqa: ANN001
    """Record the latest Alembic revision on a DB create_all just built.

    create_all produces the schema the newest migration describes but leaves
    no alembic_version row, and `alembic upgrade head` (vigilus update/init)
    replays the whole chain against an unstamped DB — failing on the first
    duplicate column. Stamping at creation time keeps the two in agreement.
    Best-effort: advisory only, must never block startup.
    """
    try:
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        from vigilus.core.preflight import _alembic_config

        cfg = _alembic_config()
        if cfg is None:
            return
        ctx = MigrationContext.configure(sync_connection)
        if ctx.get_current_revision() is not None:
            return
        ctx.stamp(ScriptDirectory.from_config(cfg), "head")
        logger.info("db.alembic_stamped", revision="head")
    except Exception:  # noqa: BLE001
        logger.exception("db.alembic_stamp_failed")


async def init_db() -> None:
    """Create all tables defined by ORM models."""
    from vigilus.db import models as _models  # noqa: F401 – ensure models are imported

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_stamp_alembic_head_if_unstamped)


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
