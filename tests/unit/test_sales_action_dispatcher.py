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


@pytest.mark.asyncio
async def test_dispatcher_handles_portfolio_lookup() -> None:
    from bookcraft.components.portfolio import PortfolioEngine, PortfolioRegistry
    from bookcraft.components.portfolio_actions import PortfolioActionService
    from bookcraft.components.portfolio_actions.repository import (
        InMemoryPortfolioViewRepository,
    )

    registry = PortfolioRegistry.from_files(
        samples_registry_path="data/portfolio/samples.registry.js",
        genre_hierarchy_path="data/portfolio/genre_hierarchy_links.json",
        portfolio_docx_path="data/portfolio/portfolio_samples.docx",
    )
    dispatcher = SalesActionDispatcher(
        portfolio_action_service=PortfolioActionService(
            portfolio_engine=PortfolioEngine(registry),
            repository=InMemoryPortfolioViewRepository(),
        )
    )

    result = await dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.PORTFOLIO_LOOKUP,
            status=ActionStatus.READY,
            collected_slots={
                "service": "interior_formatting",
                "genre": "business",
                "limit": 2,
            },
            reason="test",
        ),
        thread_id=uuid4(),
        customer_id=uuid4(),
    )

    assert result is not None
    assert result.action_type == ActionType.PORTFOLIO_LOOKUP
    assert result.success is True
    assert result.payload["service"] == "interior_formatting"
    assert result.payload["status"] in {
        "found",
        "no_match",
        "unavailable_pending",
        "unavailable_confidential",
    }


@pytest.mark.asyncio
async def test_dispatcher_handles_nda_generation_without_email() -> None:
    from pathlib import Path

    from bookcraft.components.document_actions import NDAActionService
    from bookcraft.components.document_actions.repository import (
        InMemoryDocumentRequestRepository,
    )
    from bookcraft.components.documents.engine import DocumentEngine
    from bookcraft.components.documents.registry import DocumentTemplateRegistry

    dispatcher = SalesActionDispatcher(
        nda_action_service=NDAActionService(
            document_engine=DocumentEngine(
                registry=DocumentTemplateRegistry("data/templates"),
                output_dir=Path("reports/test_documents"),
                pdf_rendering_enabled=False,
            ),
            repository=InMemoryDocumentRequestRepository(),
            email_client=None,
        )
    )

    result = await dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.GENERATE_NDA,
            status=ActionStatus.READY,
            collected_slots={
                "name": "Maya Author",
                "phone": "+1 555 123 4567",
                "email": "maya@example.com",
                "effective_date": "2026-05-18",
                "send_email": False,
            },
            reason="test",
        ),
        thread_id=uuid4(),
        customer_id=uuid4(),
    )

    assert result is not None
    assert result.success is True
    assert result.action_type == ActionType.GENERATE_NDA
    assert result.result_id is not None
    assert result.payload["recipient_email"] == "maya@example.com"


# ---------------------------------------------------------------------------
# Date/time awareness: past-date and ambiguous-date routing
# ---------------------------------------------------------------------------


def _consultation_dispatcher() -> SalesActionDispatcher:
    from bookcraft.components.consultations import (
        ConsultationActionService,
        InMemoryConsultationRepository,
    )

    return SalesActionDispatcher(
        lead_service=LeadService(repository=InMemoryLeadRepository()),
        consultation_action_service=ConsultationActionService(
            repository=InMemoryConsultationRepository()
        ),
    )


def _schedule_plan(requested_time_text: str) -> ActionPlan:
    return ActionPlan(
        action_type=ActionType.SCHEDULE_CONSULTATION,
        status=ActionStatus.READY,
        collected_slots={
            "name": "Theodora Green",
            "phone": "+1 813 846 1018",
            "email": "theodora@example.com",
            "services": ["ghostwriting"],
            "customer_timezone": "America/Chicago",
            "business_timezone": "America/Chicago",
            "requested_time_text": requested_time_text,
            "confirmed": True,
        },
        reason="test",
    )


@pytest.mark.asyncio
async def test_dispatcher_past_date_returns_clarifying_message() -> None:
    dispatcher = _consultation_dispatcher()

    result = await dispatcher.dispatch(
        _schedule_plan("January 5, 2020 at 2pm"),
        thread_id=uuid4(),
        customer_id=uuid4(),
    )

    assert result is not None
    assert result.success is False
    assert result.error_code == "requested_time_in_past"
    assert "already passed" in result.customer_safe_summary
    # Must NOT claim a booking happened.
    assert result.result_id is None


@pytest.mark.asyncio
async def test_dispatcher_ambiguous_date_returns_clarifying_message() -> None:
    from datetime import date

    dispatcher = _consultation_dispatcher()

    # Build an explicit future date and deliberately name the WRONG weekday, so the
    # weekday-vs-date cross-check fires regardless of the wall clock.
    target = date(2027, 3, 15)
    weekdays = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    wrong_weekday = weekdays[(target.weekday() + 1) % 7]
    text = f"{wrong_weekday} march 15, 2027 at 11am"

    result = await dispatcher.dispatch(
        _schedule_plan(text),
        thread_id=uuid4(),
        customer_id=uuid4(),
    )

    assert result is not None
    assert result.success is False
    assert result.error_code == "ambiguous_requested_date"
    assert "confirm" in result.customer_safe_summary.lower()
    assert result.result_id is None
