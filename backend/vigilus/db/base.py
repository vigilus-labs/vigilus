"""Async SQLAlchemy engine, session factory, and base model."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import structlog
from sqlalchemy import event
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


def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Let the scheduler, gateway, JIT polling, audit writes and chat share one
    SQLite file: WAL lets readers run alongside a writer, and busy_timeout makes
    a blocked writer wait instead of failing with "database is locked"."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _build_engine():
    settings = get_settings()
    connect_args = {}
    is_sqlite = settings.database_url.startswith("sqlite")
    if is_sqlite:
        connect_args["check_same_thread"] = False
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        connect_args=connect_args,
    )
    if is_sqlite:
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


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


def _upgrade_to_head(sync_connection, cfg, current: str) -> None:  # noqa: ANN001
    """Apply pending migrations to a stamped DB, or raise RuntimeError.

    Runs env.py on the caller's connection (config.attributes["connection"]),
    so it neither opens a second engine nor calls asyncio.run() inside the
    running loop.
    """
    from alembic import command
    from alembic.script import ScriptDirectory

    if current in ScriptDirectory.from_config(cfg).get_heads():
        return
    cfg.attributes["connection"] = sync_connection
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        raise RuntimeError(
            f"Database schema is at revision {current} and could not be upgraded "
            f"to the latest migration ({exc}). Refusing to start on an "
            "out-of-date schema. Back up the database, then run `vigilus init` "
            "to apply migrations and see the full error."
        ) from exc
    logger.info("db.migrated", from_revision=current)


async def init_db() -> None:
    """Bring the database schema up to the latest migration.

    A brand-new (unstamped) DB is built with create_all and stamped at head —
    create_all yields exactly the schema head describes. Once stamped, Alembic
    is the only schema path: create_all never adds columns to existing tables,
    so a DB behind head is migrated, and one that cannot be migrated (unknown
    revision, failing migration) refuses to start.
    """
    from vigilus.core.preflight import _alembic_config, _current_revision_sync
    from vigilus.db import models as _models  # noqa: F401 – ensure models are imported

    cfg = _alembic_config()
    engine = get_engine()
    async with engine.begin() as conn:
        current = await conn.run_sync(_current_revision_sync)
        if current is not None and cfg is not None:
            await conn.run_sync(_upgrade_to_head, cfg, current)
            return
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_stamp_alembic_head_if_unstamped)


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
