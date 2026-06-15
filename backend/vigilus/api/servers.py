from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from vigilus.db.base import get_db
from vigilus.db.models import Server
from vigilus.schemas.server import ServerCreate, ServerUpdate, ServerResponse

router = APIRouter(prefix="/servers", tags=["Servers"])

def _to_response(srv: Server) -> ServerResponse:
    return ServerResponse(
        id=srv.id,
        name=srv.name,
        hostname=srv.hostname,
        port=srv.port,
        os=srv.os,
        os_version=srv.os_version,
        tags=srv.tags or [],
        credential_id=srv.credential_id,
        notes=srv.notes,
        last_seen=srv.last_seen,
        status=srv.status.value if srv.status else "unknown",
        created_at=srv.created_at,
        updated_at=srv.updated_at
    )

@router.get("", response_model=List[ServerResponse])
async def list_servers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Server).order_by(Server.name))
    return [_to_response(s) for s in result.scalars().all()]

@router.post("", response_model=ServerResponse)
async def create_server(data: ServerCreate, db: AsyncSession = Depends(get_db)):
    srv = Server(
        name=data.name,
        hostname=data.hostname,
        port=data.port,
        os=data.os,
        os_version=data.os_version,
        tags=data.tags or [],
        credential_id=data.credential_id,
        notes=data.notes
    )
    db.add(srv)
    await db.commit()
    await db.refresh(srv)
    return _to_response(srv)

@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(server_id: str, db: AsyncSession = Depends(get_db)):
    srv = await db.get(Server, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
    return _to_response(srv)

@router.patch("/{server_id}", response_model=ServerResponse)
async def update_server(server_id: str, data: ServerUpdate, db: AsyncSession = Depends(get_db)):
    srv = await db.get(Server, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
        
    if data.name is not None: srv.name = data.name
    if data.hostname is not None: srv.hostname = data.hostname
    if data.port is not None: srv.port = data.port
    if data.os is not None: srv.os = data.os
    if data.os_version is not None: srv.os_version = data.os_version
    if data.tags is not None: srv.tags = data.tags
    if data.credential_id is not None: srv.credential_id = data.credential_id
    if data.notes is not None: srv.notes = data.notes
    
    await db.commit()
    await db.refresh(srv)
    return _to_response(srv)

@router.delete("/{server_id}")
async def delete_server(server_id: str, db: AsyncSession = Depends(get_db)):
    srv = await db.get(Server, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
    
    await db.delete(srv)
    await db.commit()
    return {"ok": True}

@router.post("/{server_id}/test")
async def test_server_connection(server_id: str, db: AsyncSession = Depends(get_db)):
    # Placeholder for actual SSH/ping test
    return {"reachable": True, "ssh_ok": True, "latency_ms": 15.2}
