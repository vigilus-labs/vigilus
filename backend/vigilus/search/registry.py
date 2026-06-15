"""Resolve the active search/fetch config and build the right backends.

Config precedence mirrors channels: a DB ``SearchConfig`` row (UI-editable)
wins over ``VIGILUS_*`` env settings. The Firecrawl API key is decrypted here
(``core/crypto.py``) and only ever held in memory.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.config import get_settings
from vigilus.search.base import FetchBackend, SearchBackend, SearchError
from vigilus.search.builtin_fetch import BuiltinFetchBackend
from vigilus.search.firecrawl import FirecrawlBackend
from vigilus.search.searxng import SearxngBackend

logger = structlog.get_logger(__name__)


@dataclass
class ResolvedSearchConfig:
    """Effective research config (DB row merged over env settings)."""

    enabled: bool
    search_backend: str          # searxng | firecrawl
    fetch_backend: str           # builtin | firecrawl
    searxng_url: str | None
    firecrawl_api_key: str | None  # decrypted; never logged
    max_results: int
    fetch_max_bytes: int
    fetch_timeout_seconds: int
    allow_private: bool
    allowed_schemes: tuple[str, ...]


async def resolve_search_config(db: AsyncSession) -> ResolvedSearchConfig:
    """Build the effective config from the DB row (if any) over env settings."""
    from vigilus.core.crypto import decrypt
    from vigilus.db.models import SearchConfig

    s = get_settings()
    row = (await db.execute(select(SearchConfig).limit(1))).scalar_one_or_none()

    search_backend = s.search_backend
    fetch_backend = s.fetch_backend
    searxng_url = s.searxng_url
    firecrawl_key = s.firecrawl_api_key
    enabled = s.search_enabled

    if row is not None:
        enabled = row.enabled
        search_backend = row.search_backend or search_backend
        fetch_backend = row.fetch_backend or fetch_backend
        searxng_url = row.searxng_url or searxng_url
        if row.firecrawl_api_key_enc:
            try:
                firecrawl_key = decrypt(row.firecrawl_api_key_enc)
            except Exception:  # noqa: BLE001 — never let a bad key crash resolution
                logger.warning("search.firecrawl_key_decrypt_failed")

    return ResolvedSearchConfig(
        enabled=enabled,
        search_backend=search_backend,
        fetch_backend=fetch_backend,
        searxng_url=searxng_url,
        firecrawl_api_key=firecrawl_key,
        max_results=s.search_max_results,
        fetch_max_bytes=s.web_fetch_max_bytes,
        fetch_timeout_seconds=s.web_fetch_timeout_seconds,
        allow_private=s.web_fetch_allow_private,
        allowed_schemes=tuple(s.web_fetch_allowed_schemes),
    )


def build_search_backend(cfg: ResolvedSearchConfig) -> SearchBackend:
    """Construct the configured search backend. Raises ``SearchError`` if unusable."""
    if cfg.search_backend == "firecrawl":
        return FirecrawlBackend(cfg.firecrawl_api_key or "")
    if cfg.search_backend == "searxng":
        return SearxngBackend(cfg.searxng_url or "")
    raise SearchError(f"Unknown search backend: {cfg.search_backend}")


async def probe_search_config(db: AsyncSession) -> dict:
    """Doctor-style reachability probe of the configured search backend.

    Returns a dict with ``ok``, ``backend``, ``detail`` and, for the SearXNG
    JSON footgun, a ``hint`` carrying the exact settings.yml fix. For Firecrawl
    it reports only whether a key is configured (never the key itself).
    """
    from vigilus.search.searxng import SEARXNG_JSON_HINT

    cfg = await resolve_search_config(db)
    out: dict = {
        "ok": False,
        "enabled": cfg.enabled,
        "search_backend": cfg.search_backend,
        "fetch_backend": cfg.fetch_backend,
        "firecrawl_key_configured": bool(cfg.firecrawl_api_key),
        "detail": "",
        "hint": None,
    }
    if not cfg.enabled:
        out["detail"] = "Web search is disabled."
        return out
    try:
        backend = build_search_backend(cfg)
        results = await backend.search("vigilus connectivity test", max_results=1)
        out["ok"] = True
        out["detail"] = f"{cfg.search_backend} reachable ({len(results)} result(s))."
    except SearchError as exc:
        msg = str(exc)
        out["detail"] = msg
        if "json" in msg.lower():
            out["hint"] = SEARXNG_JSON_HINT
    except Exception as exc:  # noqa: BLE001
        out["detail"] = str(exc)
    return out


def build_fetch_backend(cfg: ResolvedSearchConfig) -> FetchBackend:
    """Construct the configured fetch backend. Raises ``SearchError`` if unusable."""
    if cfg.fetch_backend == "firecrawl":
        return FirecrawlBackend(cfg.firecrawl_api_key or "")
    if cfg.fetch_backend == "builtin":
        return BuiltinFetchBackend(
            allow_private=cfg.allow_private,
            allowed_schemes=cfg.allowed_schemes,
            max_bytes=cfg.fetch_max_bytes,
            timeout_seconds=cfg.fetch_timeout_seconds,
        )
    raise SearchError(f"Unknown fetch backend: {cfg.fetch_backend}")
