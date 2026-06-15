"""SearXNG search backend — self-hosted meta-search, no API key.

Calls ``GET {base_url}/search?q=…&format=json``. The JSON format is **disabled
by default** in SearXNG; if the instance returns HTML we detect it and raise a
``SearchError`` carrying the exact ``settings.yml`` fix (this is the #1 setup
footgun, also surfaced by ``vigilus doctor``).
"""

from __future__ import annotations

import httpx
import structlog

from vigilus.search.base import SearchBackend, SearchError, SearchResult

logger = structlog.get_logger(__name__)

# The fix printed when an instance returns HTML instead of JSON.
SEARXNG_JSON_HINT = (
    "SearXNG returned HTML, not JSON. Enable the JSON format in the instance's "
    "settings.yml:\n  search:\n    formats:\n      - html\n      - json\n"
    "then restart SearXNG."
)


class SearxngBackend(SearchBackend):
    name = "searxng"

    def __init__(self, base_url: str, *, timeout_seconds: int = 15) -> None:
        if not base_url:
            raise SearchError("SearXNG base URL is not configured.")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        params = {"q": query, "format": "json", "safesearch": 1}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(f"{self.base_url}/search", params=params)
        except httpx.HTTPError as exc:
            raise SearchError(f"SearXNG request failed: {exc}") from exc

        ctype = resp.headers.get("content-type", "")
        if "application/json" not in ctype:
            # JSON format almost certainly not enabled on the instance.
            raise SearchError(SEARXNG_JSON_HINT)

        try:
            body = resp.json()
        except ValueError as exc:
            raise SearchError(SEARXNG_JSON_HINT) from exc

        results: list[SearchResult] = []

        # High-signal "answers" (instant-answer boxes) first, if present.
        for ans in body.get("answers", []) or []:
            if isinstance(ans, dict):
                text = ans.get("answer") or ans.get("content") or ""
                url = ans.get("url", "")
            else:
                text, url = str(ans), ""
            if text:
                results.append(SearchResult(title="Answer", url=url, snippet=str(text)))

        for item in body.get("results", []) or []:
            results.append(
                SearchResult(
                    title=item.get("title", "") or item.get("url", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", "") or "",
                    published=item.get("publishedDate"),
                )
            )
            if len(results) >= max_results:
                break

        return results[:max_results]
