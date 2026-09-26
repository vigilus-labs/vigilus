from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UsageTotals(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
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
    compression_tokens: int = 0
    total_tokens: int
    estimated_cost_usd: float | None


class UsageBudgetOperator(BaseModel):
    operator_id: str
    name: str
    limit_usd: float
    spent_usd: float
    exceeded: bool


class UsageBudget(BaseModel):
    monthly_limit_usd: float | None
    month_spent_usd: float
    percent_used: float | None
    operators: list[UsageBudgetOperator]


class UsageSummaryResponse(BaseModel):
    window: str
    cost_incomplete: bool
    totals: UsageTotals
    budget: UsageBudget
    by_actor: list[UsageByActor]
    by_provider: list[UsageByProvider]
    by_model: list[UsageByModel]
    series: list[UsageSeriesPoint]
    top_sessions: list[UsageSession]
