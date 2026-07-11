"""Tests for MCP JSON config import and bulk tool assignment."""

from __future__ import annotations

import json

from sqlalchemy import select

from vigilus.db.models import (
    McpServer,
    McpTransport,
    Operator,
    OperatorTool,
    PermissionLevel,
    Tool,
    ToolImplementationType,
)

STANDARD_CONFIG = {
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
            "env": {"SOME_KEY": "value"},
        },
        "git": {
            "command": "uvx",
            "args": ["mcp-server-git"],
        },
    }
}


class TestMcpImport:
    async def test_import_standard_config(self, async_client):
        resp = await async_client.post(
            "/api/mcp-servers/import", json={"config": json.dumps(STANDARD_CONFIG)}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["created"]) == 2
        assert body["skipped"] == []
        assert body["errors"] == []

        names = {s["name"] for s in body["created"]}
        assert names == {"filesystem", "git"}
        fs = next(s for s in body["created"] if s["name"] == "filesystem")
        assert fs["command"] == "npx"
        assert fs["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
        assert fs["env_vars"] == {"SOME_KEY": "value"}

    async def test_import_bare_mapping(self, async_client):
        bare = {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
        resp = await async_client.post("/api/mcp-servers/import", json={"config": json.dumps(bare)})
        assert resp.status_code == 200
        assert len(resp.json()["created"]) == 1

    async def test_import_single_entry(self, async_client):
        single = {"command": "npx", "args": ["-y", "some-server"]}
        resp = await async_client.post(
            "/api/mcp-servers/import", json={"config": json.dumps(single)}
        )
        assert resp.status_code == 200
        assert resp.json()["created"][0]["name"] == "imported-server"

    async def test_import_sse_url_entry(self, async_client):
        cfg = {"mcpServers": {"remote": {"url": "https://example.com/sse"}}}
        resp = await async_client.post("/api/mcp-servers/import", json={"config": json.dumps(cfg)})
        assert resp.status_code == 200
        created = resp.json()["created"][0]
        assert created["transport"] == "sse"
        assert created["sse_url"] == "https://example.com/sse"

    async def test_import_duplicate_skipped(self, async_client):
        cfg = json.dumps(STANDARD_CONFIG)
        await async_client.post("/api/mcp-servers/import", json={"config": cfg})
        resp = await async_client.post("/api/mcp-servers/import", json={"config": cfg})
        body = resp.json()
        assert body["created"] == []
        assert set(body["skipped"]) == {"filesystem", "git"}

    async def test_import_invalid_json(self, async_client):
        resp = await async_client.post("/api/mcp-servers/import", json={"config": "not json {{"})
        assert resp.status_code == 422

    async def test_import_entry_without_command_reports_error(self, async_client):
        cfg = {"mcpServers": {"broken": {"args": ["foo"]}}}
        resp = await async_client.post("/api/mcp-servers/import", json={"config": json.dumps(cfg)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] == []
        assert len(body["errors"]) == 1


class TestAssignTools:
    async def _setup(self, db_session):
        srv = McpServer(name="test-mcp", command="npx", transport=McpTransport.stdio)
        op = Operator(
            name="Test Operator",
            description="test",
            permission_level=PermissionLevel.read,
        )
        db_session.add_all([srv, op])
        await db_session.flush()
        tools = [
            Tool(
                name=f"mcp_test_{i}",
                implementation_type=ToolImplementationType.mcp,
                mcp_server_id=srv.id,
                mcp_tool_name=f"tool_{i}",
            )
            for i in range(3)
        ]
        db_session.add_all(tools)
        await db_session.commit()
        return srv, op

    async def test_assign_all_tools(self, async_client, db_session):
        srv, op = await self._setup(db_session)
        resp = await async_client.post(
            f"/api/mcp-servers/{srv.id}/assign-tools", json={"operator_ids": [op.id]}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["assigned"] == 3

        links = (
            (
                await db_session.execute(
                    select(OperatorTool).where(OperatorTool.operator_id == op.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 3

    async def test_assign_idempotent(self, async_client, db_session):
        srv, op = await self._setup(db_session)
        await async_client.post(
            f"/api/mcp-servers/{srv.id}/assign-tools", json={"operator_ids": [op.id]}
        )
        resp = await async_client.post(
            f"/api/mcp-servers/{srv.id}/assign-tools", json={"operator_ids": [op.id]}
        )
        assert resp.json()["assigned"] == 0  # already assigned

    async def test_assign_no_tools_yet(self, async_client, db_session):
        srv = McpServer(name="empty-mcp", command="npx", transport=McpTransport.stdio)
        db_session.add(srv)
        await db_session.commit()
        resp = await async_client.post(
            f"/api/mcp-servers/{srv.id}/assign-tools", json={"operator_ids": []}
        )
        assert resp.status_code == 400
        assert "Start the server" in resp.json()["detail"]
