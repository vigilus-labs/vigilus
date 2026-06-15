"""Robustness tests for MCP host lifecycle reconciliation + self-healing.

Covers Bug 2: the in-memory connections dict and DB status must never disagree
about whether a server is running, and call_tool should recover a transiently
dead connection instead of surfacing a misleading "is not running".
"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.db.models import McpServer, McpServerStatus
from vigilus.mcp_host.manager import McpManager


@pytest.fixture
def manager(db_session: AsyncSession):
    """Fresh McpManager. The code enforces a process singleton, so reset it."""
    McpManager._instance = None
    m = McpManager()
    yield m
    McpManager._instance = None


async def _seed(db_session: AsyncSession, sid: str, name: str) -> McpServer:
    srv = McpServer(id=sid, name=name, command="x", status=McpServerStatus.running)
    db_session.add(srv)
    await db_session.commit()
    return srv


# ── _on_connection_exit reconciles DB + in-memory dict ───────────────────


@pytest.mark.asyncio
async def test_on_exit_crashed_sets_error_and_pops(manager, db_session):
    srv = await _seed(db_session, "s1", "N1")
    manager.connections["s1"] = MagicMock()  # pretend a live connection existed

    await manager._on_connection_exit("s1", crashed=True, error_msg="boom")

    assert "s1" not in manager.connections
    await db_session.refresh(srv)
    assert srv.status == McpServerStatus.error
    assert srv.last_error == "boom"


@pytest.mark.asyncio
async def test_on_exit_clean_sets_stopped(manager, db_session):
    srv = await _seed(db_session, "s2", "N2")
    manager.connections["s2"] = MagicMock()

    await manager._on_connection_exit("s2", crashed=False, error_msg=None)

    assert "s2" not in manager.connections
    await db_session.refresh(srv)
    assert srv.status == McpServerStatus.stopped


@pytest.mark.asyncio
async def test_on_exit_unknown_server_is_safe(manager):
    # Server was deleted while the task was dying — must not blow up.
    await manager._on_connection_exit("ghost", crashed=True, error_msg="x")
    assert "ghost" not in manager.connections


# ── _try_restart ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_try_restart_returns_false_when_server_missing(manager):
    assert await manager._try_restart("does-not-exist") is False
    assert "does-not-exist" not in manager.connections


# ── call_tool self-healing wiring ────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_tool_self_heals_by_restarting(manager, monkeypatch):
    restarted = {"called": False}

    async def fake_restart(sid):
        restarted["called"] = True
        conn = MagicMock()
        conn.session = MagicMock()  # live session

        async def fake_inner(name, arguments):
            return {"ok": f"{name}:{arguments}"}

        conn.call_tool = fake_inner
        manager.connections[sid] = conn
        return True

    async def fake_ready(conn, timeout=15.0):
        return True

    monkeypatch.setattr(manager, "_try_restart", fake_restart)
    monkeypatch.setattr(manager, "_await_ready", fake_ready)

    result = await manager.call_tool("sX", "ping", {"a": 1})

    assert restarted["called"] is True
    assert result == {"ok": "ping:{'a': 1}"}


@pytest.mark.asyncio
async def test_call_tool_raises_when_restart_fails(manager, monkeypatch):
    async def fake_restart(sid):
        return False

    monkeypatch.setattr(manager, "_try_restart", fake_restart)

    with pytest.raises(RuntimeError, match="is not running"):
        await manager.call_tool("sY", "ping", {})


@pytest.mark.asyncio
async def test_call_tool_uses_existing_connection_without_restart(manager, monkeypatch):
    """An already-live connection must NOT trigger a restart attempt."""
    conn = MagicMock()
    conn.session = MagicMock()

    async def fake_inner(name, arguments):
        return {"ok": name}

    conn.call_tool = fake_inner
    manager.connections["sZ"] = conn

    async def should_not_be_called(sid):
        raise AssertionError("restart should not happen for a live connection")

    monkeypatch.setattr(manager, "_try_restart", should_not_be_called)

    assert await manager.call_tool("sZ", "ping", {}) == {"ok": "ping"}
