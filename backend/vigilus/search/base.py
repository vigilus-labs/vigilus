"""Search/fetch abstractions shared by every backend.

Mirrors ``providers/base.py``: small dataclasses + ABCs so the registry can
hand the rest of the system a uniform interface regardless of which backend
(SearXNG, Firecrawl, builtin) is configured.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class SearchError(Exception):
    """Raised when a search/fetch backend cannot fulfil a request.

    Carries a model-readable message — surfaced back to the orchestrator so it
    can reason about the failure (e.g. "SearXNG returned HTML, enable json").
    """


@dataclass
class SearchResult:
    """One ranked web search hit."""

    title: str
    url: str
    snippet: str
    published: str | None = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            **({"published": self.published} if self.published else {}),
        }


@dataclass
class FetchedPage:
    """A single fetched page, cleaned and truncated for an LLM to read."""

    url: str  # final URL after redirects
    title: str | None
    text: str  # cleaned / markdown, truncated
    status_code: int | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "status_code": self.status_code,
        }


class SearchBackend(ABC):
    """A web search provider (SearXNG, Firecrawl, …)."""

    name: str = "search"

    @abstractmethod
    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        """Return ranked results for *query*. Raise ``SearchError`` on failure."""
        raise NotImplementedError


class FetchBackend(ABC):
    """A single-URL page fetcher (builtin SSRF-safe httpx, Firecrawl scrape)."""

    name: str = "fetch"

    @abstractmethod
    async def fetch(self, url: str) -> FetchedPage:
        """Fetch *url* and return cleaned page text. Raise ``SearchError`` on failure."""
        raise NotImplementedError
