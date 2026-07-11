"""API routes for Provider management."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.crypto import encrypt
from vigilus.db.base import get_db
from vigilus.db.models import Provider
from vigilus.providers.registry import build_provider
from vigilus.schemas.provider import ProviderCreate, ProviderResponse, ProviderUpdate

router = APIRouter(prefix="/providers", tags=["Providers"])


def _to_response(provider: Provider) -> ProviderResponse:
    """Convert DB model to response schema, masking API key."""
    return ProviderResponse(
        id=provider.id,
        name=provider.name,
        type=provider.type.value,
        base_url=provider.base_url,
        has_api_key=bool(provider.api_key),
        default_model=provider.default_model,
        extra_headers=provider.extra_headers,
        tool_calling_supported=provider.tool_calling_supported,
        enabled=provider.enabled,
        is_default=provider.is_default,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get("", response_model=list[ProviderResponse])
async def list_providers(db: AsyncSession = Depends(get_db)):
    """List all configured LLM providers."""
    result = await db.execute(select(Provider).order_by(Provider.name))
    providers = result.scalars().all()
    return [_to_response(p) for p in providers]


@router.post("", response_model=ProviderResponse)
async def create_provider(data: ProviderCreate, db: AsyncSession = Depends(get_db)):
    """Create a new LLM provider."""
    # Check name unique
    existing = await db.execute(select(Provider).where(Provider.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Provider name already exists")

    provider = Provider(
        name=data.name,
        type=data.type,
        base_url=data.base_url,
        default_model=data.default_model,
        extra_headers=data.extra_headers,
    )

    if data.is_default:
        existing_defaults = await db.execute(
            select(Provider).where(Provider.is_default.is_(True))
        )  # noqa: E712
        for p in existing_defaults.scalars().all():
            p.is_default = False
    provider.is_default = data.is_default

    if data.api_key:
        provider.api_key = encrypt(data.api_key)

    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return _to_response(provider)


@router.patch("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: str, data: ProviderUpdate, db: AsyncSession = Depends(get_db)
):
    """Update an existing LLM provider."""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if data.name is not None and data.name != provider.name:
        existing = await db.execute(select(Provider).where(Provider.name == data.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Provider name already exists")
        provider.name = data.name

    if data.type is not None:
        provider.type = data.type
    if data.base_url is not None:
        provider.base_url = data.base_url
    if data.default_model is not None:
        provider.default_model = data.default_model
    if data.extra_headers is not None:
        provider.extra_headers = data.extra_headers
    if data.enabled is not None:
        provider.enabled = data.enabled

    if data.is_default is not None and data.is_default:
        # Clear existing default(s) first
        existing_defaults = await db.execute(
            select(Provider).where(Provider.is_default.is_(True))
        )  # noqa: E712
        for p in existing_defaults.scalars().all():
            p.is_default = False
    if data.is_default is not None:
        provider.is_default = data.is_default

    if data.api_key is not None:
        if data.api_key == "":  # clear it
            provider.api_key = None
        else:
            provider.api_key = encrypt(data.api_key)

    await db.commit()
    await db.refresh(provider)
    return _to_response(provider)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: str, db: AsyncSession = Depends(get_db)):
    """Delete an LLM provider."""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    await db.delete(provider)
    await db.commit()
    return {"ok": True}


@router.get("/default", response_model=ProviderResponse | None)
async def get_default_provider(db: AsyncSession = Depends(get_db)):
    """Get the default provider."""
    result = await db.execute(
        select(Provider).where(Provider.is_default.is_(True)).limit(1)
    )  # noqa: E712
    provider = result.scalar_one_or_none()
    if not provider:
        return None
    return _to_response(provider)


@router.get("/catalog")
async def provider_catalog():
    """Curated provider presets for the guided /login setup flow."""
    from vigilus.providers.catalog import PROVIDER_CATALOG

    return {"catalog": PROVIDER_CATALOG}


@router.get("/openrouter/models")
async def openrouter_models():
    """Fetch the public OpenRouter model catalog."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            models = [
                {
                    "id": m["id"],
                    "name": m.get("name", m["id"]),
                    "context_length": m.get("context_length"),
                    "pricing": m.get("pricing", {}),
                }
                for m in data.get("data", [])
            ]
            return {"models": models}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch OpenRouter models: {e}")


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str, db: AsyncSession = Depends(get_db)):
    """Test connection to the provider and list available models if possible."""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    try:
        agent = build_provider(provider)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    result = await agent.test_connection()
    return result


@router.get("/{provider_id}/models")
async def list_models(provider_id: str, db: AsyncSession = Depends(get_db)):
    """List available models for the provider."""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    try:
        agent = build_provider(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = await agent.test_connection()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "Unknown error"))

    return {"models": result.get("models", [])}
