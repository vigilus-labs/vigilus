"""Web search + page fetch backends for the Vigilus orchestrator.

Provider-pattern package: ``SearchBackend`` / ``FetchBackend`` ABCs with
pluggable implementations (SearXNG, Firecrawl, an SSRF-safe builtin fetcher)
selected by config. Research is a Vigilus-only capability — operators never
get web tools (see SEARCH_IMPLEMENTATION_PLAN.md §1).
"""

from __future__ import annotations

from vigilus.search.base import (
    FetchBackend,
    FetchedPage,
    SearchBackend,
    SearchError,
    SearchResult,
)

__all__ = [
    "FetchBackend",
    "FetchedPage",
    "SearchBackend",
    "SearchError",
    "SearchResult",
]
