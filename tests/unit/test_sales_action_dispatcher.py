from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.actions import ActionPlan, ActionStatus, ActionType
from bookcraft.components.actions.dispatcher import SalesActionDispatcher
from bookcraft.components.leads import LeadService
from bookcraft.components.leads.repository import InMemoryLeadRepository


@pytest.mark.asyncio
async def test_dispatcher_creates_lead() -> None:
    dispatcher = SalesActionDispatcher(
        lead_service=LeadService(repository=InMemoryLeadRepository())
    )

    result = await dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.CREATE_LEAD,
            status=ActionStatus.READY,
            collected_slots={
                "email": "author@example.com",
                "services": ["editing_proofreading"],
            },
            recommended_follow_up_slots=["name", "phone"],
            reason="test",
        ),
        thread_id=uuid4(),
        customer_id=uuid4(),
    )

    assert result is not None
    assert result.success is True
    assert result.action_type == ActionType.CREATE_LEAD
    assert result.result_id is not None
    assert result.payload["lead"]["email"] == "author@example.com"
    assert result.payload["created"] is True
    assert result.payload["recommended_follow_up_slots"] == ["name", "phone"]


@pytest.mark.asyncio
async def test_dispatcher_reports_missing_contact() -> None:
    dispatcher = SalesActionDispatcher(
        lead_service=LeadService(repository=InMemoryLeadRepository())
    )

    result = await dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.CREATE_LEAD,
            status=ActionStatus.READY,
            collected_slots={},
            reason="test",
        ),
        thread_id=uuid4(),
        customer_id=uuid4(),
    )

    assert result is not None
    assert result.success is False
    assert result.error_code == "missing_contact"


@pytest.mark.asyncio
async def test_dispatcher_returns_none_for_missing_info_plan() -> None:
    dispatcher = SalesActionDispatcher(
        lead_service=LeadService(repository=InMemoryLeadRepository())
    )

    result = await dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.CREATE_LEAD,
            status=ActionStatus.MISSING_INFO,
            missing_slots=["email_or_phone"],
            reason="test",
        ),
        thread_id=uuid4(),
        customer_id=uuid4(),
    )

    assert result is None


@pytest.mark.asyncio
async def test_dispatcher_handles_pricing_action_missing_inputs() -> None:
    from bookcraft.components.pricing import PricingTimelineEngine
    from bookcraft.components.pricing_actions import PricingActionService
    from bookcraft.components.pricing_actions.repository import (
        InMemoryPricingQuoteRepository,
    )

    dispatcher = SalesActionDispatcher(
        pricing_action_service=PricingActionService(
            pricing_engine=PricingTimelineEngine.from_config_dir(
                "data/pricing/v2",
                values_approved=False,
            ),
            repository=InMemoryPricingQuoteRepository(),
        )
    )

    result = await dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.PRICE_QUOTE,
            status=ActionStatus.READY,
            collected_slots={"services": ["editing_proofreading"]},
            reason="test",
        ),
        thread_id=uuid4(),
        customer_id=uuid4(),
    )

    assert result is not None
    assert result.action_type == ActionType.PRICE_QUOTE
    assert result.success is True
    assert result.result_id is not None
    assert result.payload["status"] == "needs_clarification"
    assert result.payload["missing_fields"]
