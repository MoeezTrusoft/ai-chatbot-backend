from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.pricing import PricingTimelineEngine
from bookcraft.components.pricing_actions import PricingActionRequest, PricingActionService
from bookcraft.components.pricing_actions.repository import InMemoryPricingQuoteRepository


@pytest.fixture()
def pricing_engine() -> PricingTimelineEngine:
    return PricingTimelineEngine.from_config_dir(
        "data/pricing/v2",
        values_approved=False,
    )


@pytest.mark.asyncio
async def test_pricing_action_returns_missing_inputs(pricing_engine: PricingTimelineEngine) -> None:
    repository = InMemoryPricingQuoteRepository()
    service = PricingActionService(pricing_engine=pricing_engine, repository=repository)

    result = await service.quote(
        PricingActionRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            services=["editing_proofreading"],
            collected_slots={"services": ["editing_proofreading"]},
        )
    )

    assert result.status == "needs_clarification"
    assert result.missing_fields
    assert repository.records
    assert repository.records[0].quote_id == result.quote_id


@pytest.mark.asyncio
async def test_pricing_action_uses_default_assumptions(
    pricing_engine: PricingTimelineEngine,
) -> None:
    repository = InMemoryPricingQuoteRepository()
    service = PricingActionService(pricing_engine=pricing_engine, repository=repository)

    result = await service.quote(
        PricingActionRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            services=["editing_proofreading"],
            collected_slots={
                "services": ["editing_proofreading"],
                "use_default_assumptions": True,
            },
            use_default_assumptions=True,
        )
    )

    assert result.used_default_assumptions is True
    assert result.assumptions is not None
    assert "editing_proofreading" in result.assumptions
    assert repository.records[0].used_default_assumptions is True


@pytest.mark.asyncio
async def test_pricing_action_rejects_invalid_services(
    pricing_engine: PricingTimelineEngine,
) -> None:
    repository = InMemoryPricingQuoteRepository()
    service = PricingActionService(pricing_engine=pricing_engine, repository=repository)

    with pytest.raises(ValueError, match="at least one valid service"):
        await service.quote(
            PricingActionRequest(
                customer_id=uuid4(),
                thread_id=uuid4(),
                services=["not_real"],
            )
        )
