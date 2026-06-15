"""Search/research admin API — manage the active research config + probe it.

Mounts under ``/api`` behind ``require_user``. The Firecrawl API key is
Fernet-encrypted at rest and never returned (only a ``has_firecrawl_key``
hint). The Test endpoint runs the same backend probe ``vigilus doctor`` uses
and surfaces the SearXNG-JSON setup hint inline.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.crypto import encrypt
from vigilus.db.base import get_db
from vigilus.db.models import SearchConfig
from vigilus.schemas.search import (
    SearchConfigResponse,
    SearchConfigUpsert,
    SearchTestResponse,
)
from vigilus.search.base import SearchError
from vigilus.search.registry import build_search_backend, resolve_search_config
from vigilus.search.searxng import SEARXNG_JSON_HINT

router = APIRouter(prefix="/search", tags=["Search"])
logger = structlog.get_logger(__name__)


def _to_response(cfg: SearchConfig | None) -> SearchConfigResponse:
    if cfg is None:
        from vigilus.config import get_settings

        s = get_settings()
        return SearchConfigResponse(
            search_backend=s.search_backend,
            fetch_backend=s.fetch_backend,
            searxng_url=s.searxng_url,
            enabled=s.search_enabled,
            has_firecrawl_key=bool(s.firecrawl_api_key),
        )
    return SearchConfigResponse(
        search_backend=cfg.search_backend,
        fetch_backend=cfg.fetch_backend,
        searxng_url=cfg.searxng_url,
        enabled=cfg.enabled,
        has_firecrawl_key=bool(cfg.firecrawl_api_key_enc),
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


@router.get("/config", response_model=SearchConfigResponse)
async def get_config(db: AsyncSession = Depends(get_db)):
    cfg = (await db.execute(select(SearchConfig).limit(1))).scalar_one_or_none()
    return _to_response(cfg)


@router.put("/config", response_model=SearchConfigResponse)
async def upsert_config(data: SearchConfigUpsert, db: AsyncSession = Depends(get_db)):
    cfg = (await db.execute(select(SearchConfig).limit(1))).scalar_one_or_none()
    key_enc = encrypt(data.firecrawl_api_key) if data.firecrawl_api_key else None
    if cfg is None:
        cfg = SearchConfig(
            search_backend=data.search_backend,
            fetch_backend=data.fetch_backend,
            searxng_url=data.searxng_url,
            firecrawl_api_key_enc=key_enc,
            enabled=data.enabled,
        )
        db.add(cfg)
    else:
        cfg.search_backend = data.search_backend
        cfg.fetch_backend = data.fetch_backend
        cfg.searxng_url = data.searxng_url
        cfg.enabled = data.enabled
        if data.firecrawl_api_key:
            cfg.firecrawl_api_key_enc = encrypt(data.firecrawl_api_key)
    await db.commit()
    await db.refresh(cfg)
    logger.info(
        "search.config_upserted",
        search_backend=cfg.search_backend,
        fetch_backend=cfg.fetch_backend,
        enabled=cfg.enabled,
    )
    return _to_response(cfg)


@router.post("/test", response_model=SearchTestResponse)
async def test_config(db: AsyncSession = Depends(get_db)):
    """Probe the configured search backend with a canned query."""
    cfg = await resolve_search_config(db)
    if not cfg.enabled:
        return SearchTestResponse(ok=False, error="Web search is disabled.")
    try:
        backend = build_search_backend(cfg)
        results = await backend.search("vigilus connectivity test", max_results=1)
    except SearchError as exc:
        msg = str(exc)
        hint = SEARXNG_JSON_HINT if "json" in msg.lower() else None
        return SearchTestResponse(ok=False, backend=cfg.search_backend, error=msg, hint=hint)
    except Exception as exc:  # noqa: BLE001
        return SearchTestResponse(ok=False, backend=cfg.search_backend, error=str(exc))
    return SearchTestResponse(
        ok=True, backend=cfg.search_backend, result_count=len(results)
    )
