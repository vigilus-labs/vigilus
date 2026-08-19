from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UsageTotals(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None


class UsageByActor(UsageTotals):
    actor_type: str
    operator_id: str | None
    name: str


class UsageByProvider(UsageTotals):
    provider_type: str | None
    provider_id: str | None = None
    name: str


class UsageByModel(UsageTotals):
    provider_type: str | None
    model: str | None
    name: str


class UsageSession(UsageTotals):
    session_id: str
    title: str | None
    last_used_at: datetime


class UsageSeriesPoint(BaseModel):
    bucket: str
    orchestrator_tokens: int
    operator_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None


class UsageSummaryResponse(BaseModel):
    window: str
    cost_incomplete: bool
    totals: UsageTotals
    by_actor: list[UsageByActor]
    by_provider: list[UsageByProvider]
    by_model: list[UsageByModel]
    series: list[UsageSeriesPoint]
    top_sessions: list[UsageSession]
