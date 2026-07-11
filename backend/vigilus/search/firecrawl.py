"""Firecrawl backend — hosted search + scrape (API key, free tier).

Implements both ``SearchBackend`` (``/v2/search``) and ``FetchBackend``
(``/v2/scrape``). Firecrawl fetches pages on *its* infrastructure, so the
builtin fetcher's SSRF rules don't apply to the scrape path — but Firecrawl
can't reach your LAN either, which keeps decision #4 (no internal fetch)
intact. The API key is passed by the caller already-decrypted; it is never
logged.
"""

from __future__ import annotations

import httpx
import structlog

from vigilus.search.base import (
    FetchBackend,
    FetchedPage,
    SearchBackend,
    SearchError,
    SearchResult,
)

logger = structlog.get_logger(__name__)

_BASE = "https://api.firecrawl.dev/v2"


class FirecrawlBackend(SearchBackend, FetchBackend):
    name = "firecrawl"

    def __init__(self, api_key: str, *, timeout_seconds: int = 20) -> None:
        if not api_key:
            raise SearchError("Firecrawl API key is not configured.")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        payload = {"query": query, "limit": max_results, "sources": ["web"]}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(f"{_BASE}/search", json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise SearchError(f"Firecrawl search failed: {exc}") from exc

        if resp.status_code == 401:
            raise SearchError("Firecrawl rejected the API key (401).")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SearchError("Firecrawl returned a non-JSON response.") from exc

        if not body.get("success", False):
            raise SearchError(f"Firecrawl search error: {body.get('error', 'unknown')}")

        credits = body.get("creditsUsed")
        if credits is not None:
            logger.info("firecrawl.search", credits_used=credits)

        web = (body.get("data") or {}).get("web") or []
        results: list[SearchResult] = []
        for item in web:
            results.append(
                SearchResult(
                    title=item.get("title", "") or item.get("url", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", "") or "",
                )
            )
            if len(results) >= max_results:
                break
        return results

    async def fetch(self, url: str) -> FetchedPage:
        payload = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": self.timeout_seconds * 1000,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds + 5) as client:
                resp = await client.post(f"{_BASE}/scrape", json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise SearchError(f"Firecrawl scrape failed: {exc}") from exc

        if resp.status_code == 401:
            raise SearchError("Firecrawl rejected the API key (401).")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SearchError("Firecrawl returned a non-JSON response.") from exc

        if not body.get("success", False):
            raise SearchError(f"Firecrawl scrape error: {body.get('error', 'unknown')}")

        data = body.get("data") or {}
        meta = data.get("metadata") or {}
        return FetchedPage(
            url=meta.get("sourceURL", url),
            title=meta.get("title"),
            text=data.get("markdown", "") or "",
            status_code=meta.get("statusCode"),
        )
