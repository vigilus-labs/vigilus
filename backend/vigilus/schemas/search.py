"""Pydantic v2 schemas for the search/research admin API.

The Firecrawl API key is write-only: it appears in the upsert body but is never
returned (only a ``has_firecrawl_key`` hint is).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SearchConfigUpsert(BaseModel):
    """Upsert body for PUT /search/config. Key optional on update."""

    search_backend: Literal["searxng", "firecrawl"] = "searxng"
    fetch_backend: Literal["builtin", "firecrawl"] = "builtin"
    searxng_url: str | None = None
    firecrawl_api_key: str | None = Field(
        default=None,
        description="Firecrawl API key (encrypted at rest). Omit on update to keep current.",
    )
    enabled: bool = True


class SearchConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    search_backend: str
    fetch_backend: str
    searxng_url: str | None
    enabled: bool
    has_firecrawl_key: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SearchTestResponse(BaseModel):
    ok: bool
    backend: str | None = None
    result_count: int | None = None
    error: str | None = None
    hint: str | None = None
