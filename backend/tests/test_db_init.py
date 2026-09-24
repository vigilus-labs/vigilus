"""Startup DB setup (db/base): SQLite pragmas and migrations as the schema path."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from vigilus.config import get_settings
from vigilus.core.preflight import alembic_heads
from vigilus.db.base import Base, close_db, get_engine, init_db


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    """Point the app's global engine at a throwaway SQLite file."""
    db = tmp_path / "vigilus.db"
    await close_db()
    monkeypatch.setenv("VIGILUS_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    yield db
    await close_db()
    monkeypatch.undo()
    get_settings.cache_clear()


def _version(db) -> str | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _columns(db, table: str) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


async def test_sqlite_connections_use_wal_and_busy_timeout(file_db):
    async with get_engine().connect() as conn:
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
        timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
    assert mode == "wal"
    assert timeout == 5000


async def test_fresh_db_is_created_and_stamped_at_head(file_db):
    await init_db()
    assert _version(file_db) == alembic_heads()[0]
    assert "os_version" in _columns(file_db, "servers")


async def _build_db_behind_head(db) -> None:
    """A DB whose schema and stamp sit at b3f1a02c7e44 for the servers table:
    c5d2b13f8e90 (servers.os_version) and everything after it are pending."""
    await init_db()
    await close_db()
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE servers DROP COLUMN os_version")
    conn.execute("UPDATE alembic_version SET version_num = 'b3f1a02c7e44'")
    conn.commit()
    conn.close()


async def test_db_behind_head_is_upgraded_on_startup(file_db):
    await _build_db_behind_head(file_db)
    assert "os_version" not in _columns(file_db, "servers")

    await init_db()

    assert "os_version" in _columns(file_db, "servers")
    assert _version(file_db) == alembic_heads()[0]


async def test_db_at_unknown_revision_refuses_to_start(file_db):
    await init_db()
    await close_db()
    conn = sqlite3.connect(file_db)
    conn.execute("UPDATE alembic_version SET version_num = 'deadbeef0000'")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="deadbeef0000"):
        await init_db()


async def test_stamped_db_does_not_get_tables_from_create_all(file_db, monkeypatch):
    # Once a DB is stamped, migrations are the only schema path: create_all
    # must not paper over drift by silently adding tables.
    await init_db()
    calls = []
    monkeypatch.setattr(Base.metadata, "create_all", lambda *a, **k: calls.append(1))
    await close_db()
    await init_db()
    assert calls == []
