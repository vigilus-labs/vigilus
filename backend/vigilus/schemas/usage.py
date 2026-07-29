from __future__ import annotations

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


class UsageSummaryResponse(BaseModel):
    window: str
    cost_incomplete: bool
    totals: UsageTotals
    by_actor: list[UsageByActor]
    by_provider: list[UsageByProvider]
