from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.db.base import get_db
from vigilus.db.models import Tool
from vigilus.schemas.tool import ToolCreate, ToolResponse, ToolUpdate

router = APIRouter(prefix="/tools", tags=["Tools"])


def _to_response(tool: Tool) -> ToolResponse:
    return ToolResponse(
        id=tool.id,
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        implementation_type=tool.implementation_type.value,
        required_permission=tool.required_permission.value,
        native_handler=tool.native_handler,
        http_config=tool.http_config,
        mcp_server_id=tool.mcp_server_id,
        mcp_tool_name=tool.mcp_tool_name,
        is_builtin=tool.is_builtin,
        available=tool.available,
        created_at=tool.created_at,
    )


@router.get("", response_model=list[ToolResponse])
async def list_tools(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tool).order_by(Tool.name))
    return [_to_response(t) for t in result.scalars().all()]


@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(tool_id: str, db: AsyncSession = Depends(get_db)):
    tool = await db.get(Tool, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    return _to_response(tool)


@router.post("", response_model=ToolResponse)
async def create_tool(data: ToolCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Tool).where(Tool.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Tool name already exists")
    tool = Tool(
        name=data.name,
        description=data.description,
        input_schema=data.input_schema,
        implementation_type=data.implementation_type,
        required_permission=data.required_permission,
        native_handler=data.native_handler,
        http_config=data.http_config,
        mcp_server_id=data.mcp_server_id,
        mcp_tool_name=data.mcp_tool_name,
        is_builtin=data.is_builtin,
        available=data.available,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return _to_response(tool)


@router.patch("/{tool_id}", response_model=ToolResponse)
async def update_tool(tool_id: str, data: ToolUpdate, db: AsyncSession = Depends(get_db)):
    tool = await db.get(Tool, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    if data.name is not None:
        existing = await db.execute(select(Tool).where(Tool.name == data.name, Tool.id != tool_id))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Tool name already exists")
        tool.name = data.name
    if data.description is not None:
        tool.description = data.description
    if data.input_schema is not None:
        tool.input_schema = data.input_schema
    if data.implementation_type is not None:
        tool.implementation_type = data.implementation_type
    if data.required_permission is not None:
        tool.required_permission = data.required_permission
    if data.native_handler is not None:
        tool.native_handler = data.native_handler
    if data.http_config is not None:
        tool.http_config = data.http_config
    if data.mcp_server_id is not None:
        tool.mcp_server_id = data.mcp_server_id
    if data.mcp_tool_name is not None:
        tool.mcp_tool_name = data.mcp_tool_name
    if data.available is not None:
        tool.available = data.available
    await db.commit()
    await db.refresh(tool)
    return _to_response(tool)


@router.delete("/{tool_id}")
async def delete_tool(tool_id: str, db: AsyncSession = Depends(get_db)):
    tool = await db.get(Tool, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    await db.delete(tool)
    await db.commit()
    return {"ok": True}
