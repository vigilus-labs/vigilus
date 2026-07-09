"""GitHub-based MCP server install lifecycle.

Covers the failure mode where "Install command failed: npm install && npm run
build" gave no clue why: install output must surface in the error, a failed
install must be retried on the next start (not skipped because the clone
already exists), and editing the install command must trigger a re-install.
"""

import os
import types

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock

from vigilus.db.models import McpServer
from vigilus.mcp_host import manager as manager_mod
from vigilus.mcp_host.manager import INSTALL_MARKER, McpConnection, McpManager, mcp_repo_path


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        manager_mod, "get_settings", lambda: types.SimpleNamespace(data_dir=str(tmp_path))
    )
    return tmp_path


@pytest.fixture
def manager():
    McpManager._instance = None
    yield McpManager()
    McpManager._instance = None


def _conn(server_id: str = "srv1", install_command: str | None = None, working_dir: str | None = None) -> McpConnection:
    return McpConnection(
        server_id=server_id,
        command="node",
        args=[],
        env=dict(os.environ),
        github_url="https://github.com/example/repo.git",
        install_command=install_command,
        working_dir=working_dir,
    )


def _seed_repo(data_dir, server_id: str = "srv1"):
    """Pre-create the clone dir so _prepare_env skips the git clone step."""
    repo = data_dir / "mcp_repos" / server_id
    repo.mkdir(parents=True)
    return repo


# ── install command execution ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_install_runs_and_writes_marker(data_dir):
    repo = _seed_repo(data_dir)
    cwd = await _conn(install_command="touch installed.txt")._prepare_env()

    assert cwd == str(repo)
    assert (repo / "installed.txt").exists()
    assert (repo / INSTALL_MARKER).exists()


@pytest.mark.asyncio
async def test_failed_install_surfaces_output(data_dir):
    repo = _seed_repo(data_dir)
    conn = _conn(install_command="echo boom-from-npm >&2; exit 3")

    with pytest.raises(RuntimeError) as exc:
        await conn._prepare_env()

    assert "boom-from-npm" in str(exc.value)
    assert "exit 3" in str(exc.value)
    assert not (repo / INSTALL_MARKER).exists()


@pytest.mark.asyncio
async def test_failed_install_is_retried_next_start(data_dir):
    repo = _seed_repo(data_dir)
    with pytest.raises(RuntimeError):
        await _conn(install_command="exit 1")._prepare_env()

    # Same clone, fixed command: install must run again, not be skipped
    # because the repo dir already exists.
    await _conn(install_command="touch fixed.txt")._prepare_env()
    assert (repo / "fixed.txt").exists()


@pytest.mark.asyncio
async def test_successful_install_not_rerun(data_dir):
    repo = _seed_repo(data_dir)
    cmd = "echo run >> count.txt"
    await _conn(install_command=cmd)._prepare_env()
    await _conn(install_command=cmd)._prepare_env()

    assert (repo / "count.txt").read_text().count("run") == 1


@pytest.mark.asyncio
async def test_changed_install_command_reruns(data_dir):
    repo = _seed_repo(data_dir)
    await _conn(install_command="touch first.txt")._prepare_env()
    await _conn(install_command="touch second.txt")._prepare_env()

    assert (repo / "second.txt").exists()


@pytest.mark.asyncio
async def test_install_timeout_kills_and_reports(data_dir, monkeypatch):
    _seed_repo(data_dir)
    monkeypatch.setattr(manager_mod, "INSTALL_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(RuntimeError, match="timed out"):
        await _conn(install_command="sleep 30")._prepare_env()


@pytest.mark.asyncio
async def test_working_dir_appended_to_repo(data_dir):
    repo = _seed_repo(data_dir)
    cwd = await _conn(working_dir="src/sub")._prepare_env()
    assert cwd == str(repo / "src" / "sub")


@pytest.mark.asyncio
async def test_non_github_server_untouched(data_dir):
    conn = McpConnection(server_id="x", command="npx", args=[], env={}, working_dir="/opt/tools")
    assert await conn._prepare_env() == "/opt/tools"


# ── reinstall / delete repo cleanup ──────────────────────────────────────


@pytest.mark.asyncio
async def test_reinstall_requires_github_server(
    data_dir, manager, db_session: AsyncSession, async_client: AsyncClient
):
    res = await async_client.post("/api/mcp-servers", json={"name": "plain", "command": "echo"})
    server_id = res.json()["id"]

    res = await async_client.post(f"/api/mcp-servers/{server_id}/reinstall")
    assert res.status_code == 400

    res = await async_client.post("/api/mcp-servers/missing/reinstall")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_reinstall_wipes_repo_and_restarts(
    data_dir, manager, db_session: AsyncSession, async_client: AsyncClient, monkeypatch
):
    srv = McpServer(
        id="gh1", name="gh", command="node", github_url="https://github.com/o/r.git"
    )
    db_session.add(srv)
    await db_session.commit()
    repo = _seed_repo(data_dir, "gh1")

    start = AsyncMock()
    monkeypatch.setattr(McpManager, "start_server", start)

    res = await async_client.post("/api/mcp-servers/gh1/reinstall")
    assert res.status_code == 200
    assert not repo.exists()
    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_removes_cloned_repo(
    data_dir, manager, db_session: AsyncSession, async_client: AsyncClient
):
    srv = McpServer(
        id="gh2", name="gh2", command="node", github_url="https://github.com/o/r.git"
    )
    db_session.add(srv)
    await db_session.commit()
    repo = _seed_repo(data_dir, "gh2")

    res = await async_client.delete("/api/mcp-servers/gh2")
    assert res.status_code == 200
    assert not repo.exists()
    assert not os.path.exists(mcp_repo_path("gh2"))
