from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from pydantic import BaseModel
from datetime import datetime

from vigilus.db.base import get_db
from vigilus.db.models import Credential, SshAuthMethod
from vigilus.core.crypto import encrypt

router = APIRouter(prefix="/credentials", tags=["Credentials"])

class CredentialCreate(BaseModel):
    name: str
    type: str
    ssh_auth_method: str | None = None
    username: str | None = None
    secret: str
    passphrase: str | None = None

class CredentialUpdate(BaseModel):
    name: str | None = None
    ssh_auth_method: str | None = None
    username: str | None = None
    secret: str | None = None
    passphrase: str | None = None

class CredentialResponse(BaseModel):
    id: str
    name: str
    type: str
    ssh_auth_method: str | None = None
    username: str | None = None
    has_secret: bool
    has_passphrase: bool
    created_at: datetime

def _to_response(cred: Credential) -> CredentialResponse:
    return CredentialResponse(
        id=cred.id,
        name=cred.name,
        type=cred.type.value,
        ssh_auth_method=cred.ssh_auth_method.value if cred.ssh_auth_method else None,
        username=cred.username,
        has_secret=bool(cred.secret),
        has_passphrase=bool(cred.passphrase),
        created_at=cred.created_at
    )

@router.get("", response_model=List[CredentialResponse])
async def list_credentials(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credential).order_by(Credential.name))
    return [_to_response(c) for c in result.scalars().all()]

@router.post("", response_model=CredentialResponse)
async def create_credential(data: CredentialCreate, db: AsyncSession = Depends(get_db)):
    cred = Credential(
        name=data.name,
        type=data.type,
        ssh_auth_method=SshAuthMethod(data.ssh_auth_method) if data.ssh_auth_method else None,
        username=data.username,
        secret=encrypt(data.secret) if data.secret else None,
        passphrase=encrypt(data.passphrase) if data.passphrase else None
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return _to_response(cred)

@router.put("/{credential_id}", response_model=CredentialResponse)
async def update_credential(credential_id: str, data: CredentialUpdate, db: AsyncSession = Depends(get_db)):
    cred = await db.get(Credential, credential_id)
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    if data.name is not None:
        cred.name = data.name
    if data.ssh_auth_method is not None:
        cred.ssh_auth_method = SshAuthMethod(data.ssh_auth_method)
    if data.username is not None:
        cred.username = data.username
    if data.secret is not None:
        cred.secret = encrypt(data.secret)
    if data.passphrase is not None:
        cred.passphrase = encrypt(data.passphrase)
    await db.commit()
    await db.refresh(cred)
    return _to_response(cred)


@router.delete("/{credential_id}")
async def delete_credential(credential_id: str, db: AsyncSession = Depends(get_db)):
    cred = await db.get(Credential, credential_id)
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    await db.delete(cred)
    await db.commit()
    return {"ok": True}
