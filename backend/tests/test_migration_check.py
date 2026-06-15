"""Startup migration-drift detection (core/preflight.check_migration_status)."""

from __future__ import annotations

import sqlite3

from vigilus.core.preflight import alembic_heads, check_migration_status


def test_alembic_heads_non_empty():
    heads = alembic_heads()
    assert heads, "expected at least one migration head"


async def test_unstamped_db_is_treated_as_ok(tmp_path):
    # A fresh DB with no alembic_version table (e.g. created by create_all) is
    # not flagged — its schema matches the models even if unstamped.
    db = tmp_path / "fresh.db"
    status = await check_migration_status(f"sqlite+aiosqlite:///{db}")
    assert status.current is None
    assert status.stamped is False
    assert status.up_to_date is True


async def test_db_behind_head_is_flagged(tmp_path):
    # Stamp a DB at a known OLD revision and confirm drift is detected.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    conn.execute("INSERT INTO alembic_version (version_num) VALUES ('b3f1a02c7e44')")
    conn.commit()
    conn.close()

    status = await check_migration_status(f"sqlite+aiosqlite:///{db}")
    assert status.current == "b3f1a02c7e44"
    assert status.stamped is True
    assert status.up_to_date is False
    assert "behind" in status.detail.lower()
    assert "vigilus init" in status.detail


async def test_db_at_head_is_ok(tmp_path):
    db = tmp_path / "head.db"
    head = alembic_heads()[0]
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (head,))
    conn.commit()
    conn.close()

    status = await check_migration_status(f"sqlite+aiosqlite:///{db}")
    assert status.current == head
    assert status.up_to_date is True
