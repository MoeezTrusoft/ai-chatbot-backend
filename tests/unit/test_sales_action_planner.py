from __future__ import annotations

from bookcraft.components.actions import ActionStatus, ActionType, SalesActionPlanner
from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState


def _message(text: str, atoms: dict[str, object] | None = None) -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms=atoms or {},
        embedding=[],
        language="en",
        char_count=len(text),
    )


def _intent(
    query: QueryIntentType,
    service: ServiceCategory | None = None,
    secondary: list[ServiceCategory] | None = None,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        query_secondary=[],
        service_primary=service,
        service_secondary=secondary or [],
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=True,
        confidence=0.95,
        rationale="test",
        evidence=[],
    )


def test_lead_with_email_is_ready_but_recommends_name_and_phone() -> None:
    planner = SalesActionPlanner()
    plan = planner.plan(
        processed=_message(
            "Email me at author@example.com",
            atoms={"emails": ["author@example.com"]},
        ),
        state=ThreadState(),
        intent=_intent(QueryIntentType.CONTACT_INFO_PROVIDED),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.CREATE_LEAD
    assert plan.status == ActionStatus.READY
    assert plan.collected_slots["email"] == "author@example.com"
    assert plan.recommended_follow_up_slots == ["name", "phone"]


def test_lead_without_email_or_phone_is_missing_contact() -> None:
    planner = SalesActionPlanner()
    plan = planner.plan(
        processed=_message("I am interested."),
        state=ThreadState(),
        intent=_intent(QueryIntentType.READY_TO_BUY),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.CREATE_LEAD
    assert plan.status == ActionStatus.MISSING_INFO
    assert plan.missing_slots == ["email_or_phone"]


def test_schedule_request_missing_name_contact_and_time() -> None:
    planner = SalesActionPlanner()
    plan = planner.plan(
        processed=_message("Schedule a consultation."),
        state=ThreadState(),
        intent=_intent(QueryIntentType.CONSULTATION_REQUEST),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.SCHEDULE_CONSULTATION
    assert plan.status == ActionStatus.MISSING_INFO
    assert plan.missing_slots == [
        "name",
        "email_or_phone",
        "preferred_date_or_time_window",
    ]


def test_schedule_pending_slot_yes_resolves_confirmation() -> None:
    planner = SalesActionPlanner()
    state = ThreadState()
    state.sales_actions.pending_confirmation.type = ActionType.SCHEDULE_CONSULTATION.value
    state.sales_actions.pending_confirmation.payload = {
        "csr_name": "Jerry Miller",
        "houston_display_time": "Tuesday 4:00 PM",
    }

    plan = planner.plan(
        processed=_message("yes"),
        state=state,
        intent=_intent(QueryIntentType.UNCLEAR),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.SCHEDULE_CONSULTATION
    assert plan.status == ActionStatus.READY
    assert plan.collected_slots["confirmed"] is True


def test_pricing_missing_info_asks_for_required_slots() -> None:
    planner = SalesActionPlanner()
    plan = planner.plan(
        processed=_message("Give me a quote."),
        state=ThreadState(),
        intent=_intent(
            QueryIntentType.PRICING_QUESTION,
            service=ServiceCategory.EDITING_PROOFREADING,
        ),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.PRICE_QUOTE
    assert plan.status == ActionStatus.MISSING_INFO
    assert "word_or_page_count" in plan.missing_slots
    assert "genre" in plan.missing_slots
    assert "manuscript_status" in plan.missing_slots
    assert "deadline" in plan.missing_slots


def test_persistent_pricing_request_uses_default_assumption_confirmation() -> None:
    planner = SalesActionPlanner()
    state = ThreadState()
    state.sales_actions.pricing.quote_attempt_count = 1

    plan = planner.plan(
        processed=_message("Just give me a rough quote."),
        state=state,
        intent=_intent(
            QueryIntentType.PRICING_QUESTION,
            service=ServiceCategory.EDITING_PROOFREADING,
        ),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.PRICE_QUOTE
    assert plan.status == ActionStatus.NEEDS_CONFIRMATION
    assert plan.confirmation_required is True
    assert plan.collected_slots["use_default_assumptions"] is True


def test_portfolio_request_without_service_is_missing_service() -> None:
    planner = SalesActionPlanner()
    plan = planner.plan(
        processed=_message("Show me samples."),
        state=ThreadState(),
        intent=_intent(QueryIntentType.PORTFOLIO_REQUEST),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.PORTFOLIO_LOOKUP
    assert plan.status == ActionStatus.MISSING_INFO
    assert plan.missing_slots == ["service"]


def test_nda_request_missing_required_params() -> None:
    planner = SalesActionPlanner()
    plan = planner.plan(
        processed=_message("Can you send an NDA?"),
        state=ThreadState(),
        intent=_intent(QueryIntentType.NDA_REQUEST),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.GENERATE_NDA
    assert plan.status == ActionStatus.MISSING_INFO
    assert plan.missing_slots == ["name", "email", "phone", "effective_date"]


def test_agreement_without_quote_is_blocked() -> None:
    planner = SalesActionPlanner()
    plan = planner.plan(
        processed=_message("Send me the agreement."),
        state=ThreadState(),
        intent=_intent(QueryIntentType.AGREEMENT_REQUEST),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.GENERATE_AGREEMENT
    assert plan.status == ActionStatus.BLOCKED
    assert plan.missing_slots == ["quote_id"]


def test_portfolio_followup_reuses_previous_service_context() -> None:
    planner = SalesActionPlanner()
    state = ThreadState()
    state.sales_actions.portfolio.requested = True
    state.sales_actions.portfolio.requested_service = "interior_formatting"
    state.sales_actions.portfolio.genre = "default"
    state.sales_actions.portfolio.seen_sample_ids = [
        "Formatting:default:0",
        "Formatting:default:1",
        "Formatting:default:2",
    ]

    plan = planner.plan(
        processed=_message(
            "Show me more samples like those.",
            atoms={"query_cues": ["portfolio_request"]},
        ),
        state=state,
        intent=_intent(QueryIntentType.PORTFOLIO_REQUEST),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.PORTFOLIO_LOOKUP
    assert plan.status == ActionStatus.READY
    assert plan.collected_slots["service"] == "interior_formatting"
    assert plan.collected_slots["genre"] == "default"
    assert plan.collected_slots["exclude_sample_ids"] == [
        "Formatting:default:0",
        "Formatting:default:1",
        "Formatting:default:2",
    ]


def test_nda_pending_confirmation_yes_carries_payload_and_send_email() -> None:
    planner = SalesActionPlanner()
    state = ThreadState()
    state.sales_actions.pending_confirmation.type = ActionType.GENERATE_NDA.value
    state.sales_actions.pending_confirmation.payload = {
        "name": "Maya Author",
        "email": "maya@example.com",
        "phone": "+1 555 123 4567",
        "effective_date": "2026-05-18",
        "send_email": False,
    }

    plan = planner.plan(
        processed=_message("yes, send it"),
        state=state,
        intent=_intent(QueryIntentType.UNCLEAR),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.GENERATE_NDA
    assert plan.status == ActionStatus.READY
    assert plan.collected_slots["name"] == "Maya Author"
    assert plan.collected_slots["email"] == "maya@example.com"
    assert plan.collected_slots["phone"] == "+1 555 123 4567"
    assert plan.collected_slots["effective_date"] == "2026-05-18"
    assert plan.collected_slots["send_email"] is True


def test_nda_details_message_collects_name_contact_and_effective_today() -> None:
    planner = SalesActionPlanner()

    plan = planner.plan(
        processed=_message(
            "Use Maya Author, maya@example.com, +1 555 123 4567, and make the NDA effective today.",
            atoms={
                "emails": ["maya@example.com"],
                "phones": ["+1 555 123 4567"],
            },
        ),
        state=ThreadState(),
        intent=_intent(QueryIntentType.NDA_REQUEST),
        extraction=CombinedExtraction(),
    )

    assert plan.action_type == ActionType.GENERATE_NDA
    assert plan.status == ActionStatus.NEEDS_CONFIRMATION
    assert plan.confirmation_required is True
    assert plan.collected_slots["name"] == "Maya Author"
    assert plan.collected_slots["email"] == "maya@example.com"
    assert plan.collected_slots["phone"] == "+1 555 123 4567"
    assert plan.collected_slots["effective_date"]
    assert plan.collected_slots["send_email"] is False
