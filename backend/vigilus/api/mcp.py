from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from vigilus.db.base import get_db
from vigilus.db.models import (
    McpServer,
    McpServerStatus,
    McpTransport,
    Operator,
    OperatorTool,
    Tool,
)
from vigilus.schemas.mcp import McpServerCreate, McpServerUpdate, McpServerResponse
from vigilus.mcp_host.manager import McpManager

router = APIRouter(prefix="/mcp-servers", tags=["MCP Servers"])

def _to_response(srv: McpServer) -> McpServerResponse:
    return McpServerResponse(
        id=srv.id,
        name=srv.name,
        command=srv.command,
        args=srv.args,
        env_vars=srv.env_vars,
        transport=srv.transport.value,
        sse_url=srv.sse_url,
        status=srv.status.value,
        autostart=srv.autostart,
        github_url=srv.github_url,
        install_command=srv.install_command,
        working_dir=srv.working_dir,
        last_started_at=srv.last_started_at,
        last_error=srv.last_error,
        created_at=srv.created_at
    )

@router.get("", response_model=list[McpServerResponse])
async def list_mcp_servers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(McpServer).order_by(McpServer.name))
    return [_to_response(s) for s in result.scalars().all()]

@router.post("", response_model=McpServerResponse)
async def create_mcp_server(data: McpServerCreate, db: AsyncSession = Depends(get_db)):
    srv = McpServer(
        name=data.name,
        command=data.command,
        args=data.args or [],
        env_vars=data.env_vars or {},
        transport=data.transport,
        sse_url=data.sse_url,
        autostart=data.autostart,
        github_url=data.github_url,
        install_command=data.install_command,
        working_dir=data.working_dir
    )
    db.add(srv)
    await db.commit()
    await db.refresh(srv)
    
    if srv.autostart:
        manager = McpManager()
        await manager.start_server(srv)
        await db.refresh(srv)
        
    return _to_response(srv)

# ── JSON config import (Claude Desktop / Cursor "mcpServers" format) ────


class McpImportRequest(BaseModel):
    config: str  # raw pasted JSON text


class McpImportResponse(BaseModel):
    created: list[McpServerResponse]
    skipped: list[str]  # names that already existed
    errors: list[str]


def _parse_mcp_config(raw: str) -> dict[str, dict]:
    """Parse pasted MCP config text into {name: entry} form.

    Accepts the standard ``{"mcpServers": {...}}`` wrapper, a bare
    ``{name: {command, args, env}}`` mapping, or a single unnamed entry
    ``{"command": ..., "args": [...]}``.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Not valid JSON: {e}. Paste the mcpServers block from the server's README.",
        )

    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Expected a JSON object.")

    if "mcpServers" in data and isinstance(data["mcpServers"], dict):
        servers = data["mcpServers"]
    elif "command" in data or "url" in data:
        servers = {"imported-server": data}
    else:
        servers = data

    if not servers or not all(isinstance(v, dict) for v in servers.values()):
        raise HTTPException(
            status_code=422,
            detail='No server entries found. Expected {"mcpServers": {"name": {"command": ...}}}.',
        )
    return servers


@router.post("/import", response_model=McpImportResponse)
async def import_mcp_servers(data: McpImportRequest, db: AsyncSession = Depends(get_db)):
    """Import MCP servers from pasted standard mcpServers JSON config."""
    entries = _parse_mcp_config(data.config)

    created: list[McpServerResponse] = []
    skipped: list[str] = []
    errors: list[str] = []

    for name, entry in entries.items():
        existing = (await db.execute(
            select(McpServer).where(McpServer.name == name)
        )).scalar_one_or_none()
        if existing:
            skipped.append(name)
            continue

        url = entry.get("url") or entry.get("sse_url")
        command = entry.get("command")
        if not command and not url:
            errors.append(f"'{name}': entry has neither 'command' nor 'url' — skipped.")
            continue

        env = entry.get("env") or entry.get("env_vars") or {}
        args = entry.get("args") or []
        if not isinstance(args, list):
            errors.append(f"'{name}': 'args' must be a list — skipped.")
            continue

        srv = McpServer(
            name=name,
            command=command or "",
            args=[str(a) for a in args],
            env_vars={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            transport=McpTransport.sse if url and not command else McpTransport.stdio,
            sse_url=url,
            autostart=False,
        )
        db.add(srv)
        await db.flush()
        created.append(_to_response(srv))

    await db.commit()
    return McpImportResponse(created=created, skipped=skipped, errors=errors)


# ── Bulk tool assignment ────────────────────────────────────────────────


class AssignToolsRequest(BaseModel):
    operator_ids: list[str]


@router.post("/{server_id}/assign-tools")
async def assign_server_tools(
    server_id: str, data: AssignToolsRequest, db: AsyncSession = Depends(get_db)
):
    """Assign all of this MCP server's discovered tools to the given operators."""
    srv = await db.get(McpServer, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")

    tools = (await db.execute(
        select(Tool).where(Tool.mcp_server_id == server_id)
    )).scalars().all()
    if not tools:
        raise HTTPException(
            status_code=400,
            detail="No tools discovered for this server yet. Start the server first "
            "so its tools can be synced.",
        )

    assigned = 0
    for operator_id in data.operator_ids:
        operator = await db.get(Operator, operator_id)
        if not operator:
            raise HTTPException(status_code=400, detail=f"Operator not found: {operator_id}")
        for tool in tools:
            existing = (await db.execute(
                select(OperatorTool).where(
                    OperatorTool.operator_id == operator_id,
                    OperatorTool.tool_id == tool.id,
                )
            )).scalar_one_or_none()
            if not existing:
                db.add(OperatorTool(operator_id=operator_id, tool_id=tool.id))
                assigned += 1

    await db.commit()
    return {"ok": True, "tools": len(tools), "operators": len(data.operator_ids), "assigned": assigned}


@router.get("/{server_id}", response_model=McpServerResponse)
async def get_mcp_server(server_id: str, db: AsyncSession = Depends(get_db)):
    srv = await db.get(McpServer, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
    return _to_response(srv)

@router.patch("/{server_id}", response_model=McpServerResponse)
async def update_mcp_server(server_id: str, data: McpServerUpdate, db: AsyncSession = Depends(get_db)):
    srv = await db.get(McpServer, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
        
    if data.name is not None: srv.name = data.name
    if data.command is not None: srv.command = data.command
    if data.args is not None: srv.args = data.args
    if data.env_vars is not None: srv.env_vars = data.env_vars
    if data.transport is not None: srv.transport = data.transport
    if data.sse_url is not None: srv.sse_url = data.sse_url
    if data.autostart is not None: srv.autostart = data.autostart
    if hasattr(data, "github_url") and data.github_url is not None: srv.github_url = data.github_url
    if hasattr(data, "install_command") and data.install_command is not None: srv.install_command = data.install_command
    if hasattr(data, "working_dir") and data.working_dir is not None: srv.working_dir = data.working_dir
    
    await db.commit()
    await db.refresh(srv)
    return _to_response(srv)

@router.delete("/{server_id}")
async def delete_mcp_server(server_id: str, db: AsyncSession = Depends(get_db)):
    srv = await db.get(McpServer, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
        
    manager = McpManager()
    await manager.stop_server(server_id)
    manager.remove_repo(server_id)  # don't orphan the managed clone on disk

    await db.delete(srv)
    await db.commit()
    return {"ok": True}

@router.post("/{server_id}/start", response_model=McpServerResponse)
async def start_mcp_server(server_id: str, db: AsyncSession = Depends(get_db)):
    srv = await db.get(McpServer, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
        
    manager = McpManager()
    await manager.start_server(srv)
    await db.refresh(srv)
    return _to_response(srv)

@router.post("/{server_id}/reinstall", response_model=McpServerResponse)
async def reinstall_mcp_server(server_id: str, db: AsyncSession = Depends(get_db)):
    """Wipe a GitHub-based server's managed clone and start it again,
    forcing a fresh clone + install run."""
    srv = await db.get(McpServer, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
    if not srv.github_url:
        raise HTTPException(
            status_code=400,
            detail="Only GitHub-based servers have a managed install to redo.",
        )

    manager = McpManager()
    await manager.stop_server(server_id)
    manager.remove_repo(server_id)
    await manager.start_server(srv)
    await db.refresh(srv)
    return _to_response(srv)


@router.post("/{server_id}/stop", response_model=McpServerResponse)
async def stop_mcp_server(server_id: str, db: AsyncSession = Depends(get_db)):
    srv = await db.get(McpServer, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
        
    manager = McpManager()
    await manager.stop_server(server_id)
    await db.refresh(srv)
    return _to_response(srv)
