"""Tests for LLM usage metering (model, record, aggregate)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from vigilus.db.models import LlmUsage, UsageActorType


@pytest.mark.asyncio
async def test_llm_usage_model_round_trip(db_session):
    row = LlmUsage(
        actor_type=UsageActorType.orchestrator,
        operator_id=None,
        session_id=None,
        provider_id=None,
        provider_type="openrouter",
        model="openai/gpt-4o-mini",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.001,
    )
    db_session.add(row)
    await db_session.commit()

    loaded = (await db_session.execute(select(LlmUsage))).scalar_one()
    assert loaded.actor_type == UsageActorType.orchestrator
    assert loaded.input_tokens == 100
    assert loaded.output_tokens == 50
    assert loaded.provider_type == "openrouter"
    assert loaded.estimated_cost_usd == pytest.approx(0.001)
    assert loaded.created_at is not None
