"""Per-run caching in ToolRegistry (#50).

One tool-row lookup per tool per run, and approved-grant lookups that are
cacheable while still taking effect immediately when grant state changes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from vigilus.core.events import get_event_bus
from vigilus.core.rbac import Permission, WardenService
from vigilus.db.base import get_engine
from vigilus.db.models import JitRequest, JitStatus, Operator, PermissionLevel, Tool
from vigilus.tools.registry import ToolRegistry, _get_grant_cache


@pytest.fixture(autouse=True)
def _isolated_caches():
    """The grant cache is process-wide; give each test a clean slate."""
    _get_grant_cache().clear()
    yield
    _get_grant_cache().clear()


class _QueryCounter:
    """Count SELECTs against one table across ALL sessions of the app engine."""

    def __init__(self, table: str):
        self.table = table
        self.count = 0

    def _bump(self, statement: str) -> None:
        if f"FROM {self.table}" in statement:
            self.count += 1

    def __enter__(self):
        self._handle = (
            lambda conn, cursor, statement, parameters, context, executemany: self._bump(
                statement
            )
        )
        event.listen(get_engine().sync_engine, "before_cursor_execute", self._handle)
        return self

    def __exit__(self, *exc):
        event.remove(get_engine().sync_engine, "before_cursor_execute", self._handle)
        return False


async def _add_tool(db_session, tool_id: str, name: str) -> Tool:
    # native_handler is never resolved: _stub_handler intercepts the lookup.
    tool = Tool(
        id=tool_id,
        name=name,
        required_permission=PermissionLevel.read,
        native_handler="tests:fake",
    )
    db_session.add(tool)
    await db_session.commit()
    return tool


def _stub_handler(registry: ToolRegistry, calls: list) -> None:
    """Replace dispatch so the tests exercise resolution, not a real handler."""

    async def handler(**kwargs):
        calls.append(kwargs.get("arguments"))
        return {"ok": True}

    registry._get_native_handler = lambda path: handler


@pytest.mark.asyncio
async def test_tool_row_queried_once_per_run(db_session):
    """Repeated calls to the same tool in one run issue one Tool query."""
    op = Operator(id="op-cache-1", name="op", description="test", permission_level=PermissionLevel.read)
    db_session.add(op)
    await _add_tool(db_session, "tool-a", "counter_tool")

    registry = ToolRegistry()
    calls: list = []
    _stub_handler(registry, calls)

    with _QueryCounter("tools") as counter:
        for _ in range(3):
            res = await registry.execute("counter_tool", {}, operator=op)
            assert res.success is True

    assert counter.count == 1
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_tool_cache_hits_across_id_and_name(db_session):
    """A lookup by name then by the same tool's id is still one query."""
    op = Operator(id="op-cache-2", name="op", description="test", permission_level=PermissionLevel.read)
    db_session.add(op)
    await _add_tool(db_session, "tool-xyz", "named_tool")

    registry = ToolRegistry()
    calls: list = []
    _stub_handler(registry, calls)

    with _QueryCounter("tools") as counter:
        assert (await registry.execute("named_tool", {}, operator=op)).success
        assert (await registry.execute("tool-xyz", {}, operator=op)).success

    assert counter.count == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_new_run_requeries_tools(db_session):
    """Caches are per-run: each fresh registry re-resolves the tool row."""
    op = Operator(id="op-cache-3", name="op", description="test", permission_level=PermissionLevel.read)
    db_session.add(op)
    await _add_tool(db_session, "tool-refresh", "refresh_tool")

    calls: list = []
    with _QueryCounter("tools") as counter:
        for _ in range(2):
            registry = ToolRegistry()
            _stub_handler(registry, calls)
            assert (await registry.execute("refresh_tool", {}, operator=op)).success

    assert counter.count == 2
    assert len(calls) == 2


async def _add_approved_grant(db_session, operator_id: str, description: str) -> str:
    warden = WardenService()
    token_str = warden.issue_token(operator_id, "*", Permission.exec, 15)
    db_session.add(
        JitRequest(
            operator_id=operator_id,
            resource="*",
            permission="exec",
            task_description=description,
            status=JitStatus.approved,
            token_id=token_str,
            ttl_minutes=15,
            scope_mode="timed",
        )
    )
    await db_session.commit()
    return token_str


@pytest.mark.asyncio
async def test_approved_grant_lookup_is_cached(db_session):
    """A repeated lookup for the same (operator, resource, permission) hits the cache."""
    op = Operator(id="op-grant-1", name="op", description="test", permission_level=PermissionLevel.read)
    db_session.add(op)
    await db_session.commit()
    await _add_approved_grant(db_session, "op-grant-1", "cached grant")

    registry = ToolRegistry()
    with _QueryCounter("jit_requests") as counter:
        first = await registry._find_approved_token(db_session, op, "server:web01", Permission.exec)
        second = await registry._find_approved_token(
            db_session, op, "server:web01", Permission.exec
        )

    assert first is not None and second is not None
    assert second.token_id == first.token_id
    assert counter.count == 1


@pytest.mark.asyncio
async def test_grant_cache_cleared_on_jit_resolved(db_session):
    """Publishing jit.resolved must invalidate cached grant lookups."""
    op = Operator(id="op-grant-2", name="op", description="test", permission_level=PermissionLevel.read)
    db_session.add(op)
    await db_session.commit()
    await _add_approved_grant(db_session, "op-grant-2", "grant")

    registry = ToolRegistry()
    found = await registry._find_approved_token(db_session, op, "server:web01", Permission.exec)
    assert found is not None

    await get_event_bus().publish("jit.resolved", {"reason": "test"})

    with _QueryCounter("jit_requests") as counter:
        again = await registry._find_approved_token(
            db_session, op, "server:web01", Permission.exec
        )
    assert again is not None
    assert counter.count == 1, "grant cache must have been cleared by jit.resolved"


@pytest.mark.asyncio
async def test_missing_grant_is_never_cached(db_session):
    """A 'no grant found' result must re-query so an external approval is
    visible on the very next call."""
    op = Operator(id="op-grant-3", name="op", description="test", permission_level=PermissionLevel.read)
    db_session.add(op)
    await db_session.commit()

    registry = ToolRegistry()
    for _ in range(3):
        assert await registry._find_approved_token(db_session, op, "*", Permission.exec) is None

    with _QueryCounter("jit_requests") as counter:
        assert await registry._find_approved_token(db_session, op, "*", Permission.exec) is None
    assert counter.count == 1
