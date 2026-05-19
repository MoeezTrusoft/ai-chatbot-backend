from __future__ import annotations

from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span
from bookcraft.components.tools.governance import ToolGovernanceGate
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState

_gate = ToolGovernanceGate()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _intent(
    *,
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: ServiceCategory | None = None,
    confidence: float = 0.90,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=confidence,
        rationale="test",
        evidence=[],
    )


def _processed(text: str) -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[1.0],
        language="en",
        char_count=len(text),
    )


def _plan(
    action_type: ActionType | None,
    status: ActionStatus = ActionStatus.READY,
    *,
    confirmation_required: bool = False,
    collected_slots: dict | None = None,
    missing_slots: list[str] | None = None,
) -> ActionPlan:
    return ActionPlan(
        action_type=action_type,
        status=status,
        confirmation_required=confirmation_required,
        collected_slots=collected_slots or {},
        missing_slots=missing_slots or [],
        reason="test plan",
    )


def _state_with_quote(quote_id: str = "quote-123") -> ThreadState:
    state = ThreadState()
    state.sales_actions.pricing.quote_id = quote_id
    return state


def _processed_counterfactual(text: str) -> ProcessedMessage:
    """Return a ProcessedMessage whose entire text is wrapped in a counterfactual span."""
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[Span(start=0, end=len(text), text=text, cue="if")],
        deterministic_atoms={},
        embedding=[1.0],
        language="en",
        char_count=len(text),
    )


# ---------------------------------------------------------------------------
# Rule 1: No action
# ---------------------------------------------------------------------------


def test_no_action_type_is_allowed() -> None:
    result = _gate.evaluate(
        action_plan=_plan(None, ActionStatus.NOT_NEEDED),
        intent=_intent(),
        processed=_processed("Just browsing."),
        state=ThreadState(),
    )
    assert result.allowed
    assert result.reason == "no_action"
    assert any("no_action" in a for a in result.audit)


# ---------------------------------------------------------------------------
# Read-only actions
# ---------------------------------------------------------------------------


def test_portfolio_lookup_is_always_allowed() -> None:
    result = _gate.evaluate(
        action_plan=_plan(ActionType.PORTFOLIO_LOOKUP),
        intent=_intent(query=QueryIntentType.PORTFOLIO_REQUEST, confidence=0.4),
        processed=_processed("Show me some samples."),
        state=ThreadState(),
    )
    assert result.allowed
    assert "read_only_allowed" in result.reason


def test_portfolio_lookup_allowed_even_at_low_confidence() -> None:
    result = _gate.evaluate(
        action_plan=_plan(ActionType.PORTFOLIO_LOOKUP),
        intent=_intent(confidence=0.2),
        processed=_processed("samples please"),
        state=ThreadState(),
    )
    assert result.allowed


# ---------------------------------------------------------------------------
# Non-READY passthrough
# ---------------------------------------------------------------------------


def test_missing_info_status_passes_through() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.SCHEDULE_CONSULTATION,
            ActionStatus.MISSING_INFO,
            missing_slots=["name", "email_or_phone"],
        ),
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        processed=_processed("I need a consultation."),
        state=ThreadState(),
    )
    assert result.allowed
    assert "missing_info" in result.reason


def test_needs_confirmation_status_passes_through() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.SCHEDULE_CONSULTATION,
            ActionStatus.NEEDS_CONFIRMATION,
            confirmation_required=True,
        ),
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        processed=_processed("I need a consultation next Monday."),
        state=ThreadState(),
    )
    assert result.allowed
    assert result.requires_confirmation is False  # only set when action is executed


def test_blocked_status_passes_through_without_dispatch() -> None:
    result = _gate.evaluate(
        action_plan=_plan(ActionType.GENERATE_AGREEMENT, ActionStatus.BLOCKED),
        intent=_intent(query=QueryIntentType.AGREEMENT_REQUEST),
        processed=_processed("I'd like to sign the agreement."),
        state=ThreadState(),  # no quote_id
    )
    assert result.allowed  # planner already blocked; gate passes through
    assert "blocked" in result.reason


# ---------------------------------------------------------------------------
# Rule 2: Confidence gate for write actions
# ---------------------------------------------------------------------------


def test_low_confidence_blocks_create_lead_ready() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.CREATE_LEAD,
            collected_slots={"email": "test@example.com"},
        ),
        intent=_intent(confidence=0.40),
        processed=_processed("I might need help, email: test@example.com"),
        state=ThreadState(),
    )
    assert not result.allowed
    assert "low_confidence" in result.reason
    assert result.blocked_message is not None
    assert "technical" not in result.blocked_message.lower()


def test_sufficient_confidence_allows_create_lead_ready() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.CREATE_LEAD,
            collected_slots={"email": "test@example.com"},
        ),
        intent=_intent(confidence=0.85),
        processed=_processed("My email is test@example.com"),
        state=ThreadState(),
    )
    assert result.allowed


def test_confidence_exactly_at_threshold_allows() -> None:
    result = _gate.evaluate(
        action_plan=_plan(ActionType.CREATE_LEAD, collected_slots={"email": "x@y.com"}),
        intent=_intent(confidence=0.60),
        processed=_processed("email x@y.com"),
        state=ThreadState(),
    )
    assert result.allowed  # >= threshold


def test_confidence_just_below_threshold_blocks() -> None:
    result = _gate.evaluate(
        action_plan=_plan(ActionType.CREATE_LEAD, collected_slots={"email": "x@y.com"}),
        intent=_intent(confidence=0.59),
        processed=_processed("email x@y.com"),
        state=ThreadState(),
    )
    assert not result.allowed


# ---------------------------------------------------------------------------
# Rule 3/5: NDA governance
# ---------------------------------------------------------------------------


def test_negated_nda_message_blocks_generate_nda_ready() -> None:
    result = _gate.evaluate(
        action_plan=_plan(ActionType.GENERATE_NDA),
        intent=_intent(query=QueryIntentType.NDA_REQUEST),
        processed=_processed("I don't need an NDA."),
        state=ThreadState(),
    )
    assert not result.allowed
    assert "nda" in result.reason
    assert result.blocked_message is not None


def test_valid_nda_request_allows_generate_nda_ready() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.GENERATE_NDA,
            collected_slots={
                "name": "Maya Author",
                "email": "maya@example.com",
                "phone": "555-1234",
                "effective_date": "2026-06-01",
            },
        ),
        intent=_intent(query=QueryIntentType.NDA_REQUEST),
        processed=_processed("Please send me an NDA."),
        state=ThreadState(),
    )
    assert result.allowed


def test_confirmation_turn_for_nda_allowed() -> None:
    # "yes, send it" — no "nda" word in text, should pass through
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.GENERATE_NDA,
            collected_slots={
                "name": "Maya Author",
                "email": "maya@example.com",
                "phone": "555-1234",
                "effective_date": "2026-06-01",
                "confirmed": True,
            },
        ),
        intent=_intent(query=QueryIntentType.NDA_REQUEST),
        processed=_processed("Yes, send it."),
        state=ThreadState(),
    )
    assert result.allowed


# ---------------------------------------------------------------------------
# Rule 3/6: Agreement governance
# ---------------------------------------------------------------------------


def test_negated_agreement_blocks_generate_agreement_ready() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.GENERATE_AGREEMENT,
            collected_slots={"quote_id": "q-1"},
        ),
        intent=_intent(query=QueryIntentType.AGREEMENT_REQUEST),
        processed=_processed("I am not ready for an agreement yet."),
        state=_state_with_quote("q-1"),
    )
    assert not result.allowed
    assert "agreement" in result.reason


def test_agreement_without_quote_id_in_state_is_blocked() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.GENERATE_AGREEMENT,
            collected_slots={"quote_id": "q-1"},
        ),
        intent=_intent(query=QueryIntentType.AGREEMENT_REQUEST),
        processed=_processed("I am ready to sign the agreement."),
        state=ThreadState(),  # no quote_id
    )
    assert not result.allowed
    assert "approved_quote" in result.reason


def test_valid_agreement_with_quote_is_allowed() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.GENERATE_AGREEMENT,
            collected_slots={
                "name": "Kashif Author",
                "email": "kashif@example.com",
                "phone": "555-9999",
                "quote_id": "q-abc",
                "client_location": "New York, NY",
                "effective_date": "2026-06-01",
            },
        ),
        intent=_intent(query=QueryIntentType.AGREEMENT_REQUEST),
        processed=_processed("I am ready to sign the agreement."),
        state=_state_with_quote("q-abc"),
    )
    assert result.allowed


# ---------------------------------------------------------------------------
# Rule 8/9: Pricing governance
# ---------------------------------------------------------------------------


def test_negated_pricing_blocks_price_quote_ready() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.PRICE_QUOTE,
            collected_slots={"services": ["ghostwriting"], "word_count": 50000},
        ),
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        processed=_processed("Don't send a quote yet."),
        state=ThreadState(),
    )
    assert not result.allowed
    assert "pricing" in result.reason


def test_real_pricing_request_is_allowed() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.PRICE_QUOTE,
            collected_slots={"services": ["ghostwriting"], "word_count": 50000},
        ),
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        processed=_processed("Can you give me a quote for ghostwriting?"),
        state=ThreadState(),
    )
    assert result.allowed


def test_price_quote_missing_info_always_passes_through() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.PRICE_QUOTE,
            ActionStatus.MISSING_INFO,
            missing_slots=["word_or_page_count", "genre"],
        ),
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        processed=_processed("Don't send a quote yet."),
        state=ThreadState(),
    )
    assert result.allowed  # MISSING_INFO: dispatcher won't run


# ---------------------------------------------------------------------------
# Rule 10: Idempotency key
# ---------------------------------------------------------------------------


def test_idempotency_key_present_for_allowed_write_action() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.CREATE_LEAD,
            collected_slots={"email": "a@b.com"},
        ),
        intent=_intent(confidence=0.90),
        processed=_processed("My email is a@b.com"),
        state=ThreadState(),
    )
    assert result.allowed
    assert result.idempotency_key is not None
    assert len(result.idempotency_key) == 24


def test_idempotency_key_absent_for_non_ready_action() -> None:
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.CREATE_LEAD,
            ActionStatus.MISSING_INFO,
        ),
        intent=_intent(confidence=0.90),
        processed=_processed("I need help"),
        state=ThreadState(),
    )
    assert result.idempotency_key is None


def test_idempotency_key_is_deterministic() -> None:
    plan = _plan(ActionType.CREATE_LEAD, collected_slots={"email": "x@y.com"})
    intent = _intent(confidence=0.90)
    processed = _processed("My email is x@y.com")
    state = ThreadState()
    r1 = _gate.evaluate(action_plan=plan, intent=intent, processed=processed, state=state)
    r2 = _gate.evaluate(action_plan=plan, intent=intent, processed=processed, state=state)
    assert r1.idempotency_key == r2.idempotency_key


# ---------------------------------------------------------------------------
# Rule 11: Customer-safe messages
# ---------------------------------------------------------------------------


def test_blocked_message_is_non_technical() -> None:
    result = _gate.evaluate(
        action_plan=_plan(ActionType.CREATE_LEAD, collected_slots={"email": "a@b.com"}),
        intent=_intent(confidence=0.30),
        processed=_processed("maybe"),
        state=ThreadState(),
    )
    assert not result.allowed
    assert result.blocked_message is not None
    for tech_term in ["ContextArbiter", "ActionPlan", "governance", "threshold", "0.60"]:
        assert tech_term not in result.blocked_message


def test_audit_trail_is_populated() -> None:
    result = _gate.evaluate(
        action_plan=_plan(None, ActionStatus.NOT_NEEDED),
        intent=_intent(),
        processed=_processed("Hi"),
        state=ThreadState(),
    )
    assert result.audit


# ===========================================================================
# Required tests (exact names from spec)
# ===========================================================================


def test_allows_no_action_plan() -> None:
    """action_type None or NOT_NEEDED → allowed, reason indicates no_action."""
    result = _gate.evaluate(
        action_plan=_plan(None, ActionStatus.NOT_NEEDED),
        intent=_intent(),
        processed=_processed("Just looking around."),
        state=ThreadState(),
    )
    assert result.allowed
    assert "no_action" in result.reason


def test_allows_pricing_missing_info() -> None:
    """PRICE_QUOTE with MISSING_INFO status only asks for data — no side effect."""
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.PRICE_QUOTE,
            ActionStatus.MISSING_INFO,
            missing_slots=["genre", "word_or_page_count", "deadline"],
            collected_slots={"services": ["ghostwriting"]},
        ),
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        processed=_processed("How much does ghostwriting cost?"),
        state=ThreadState(),
    )
    assert result.allowed


def test_blocks_low_confidence_consultation_booking() -> None:
    """schedule_consultation READY blocked when intent confidence is below threshold."""
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.SCHEDULE_CONSULTATION,
            ActionStatus.READY,
            collected_slots={
                "name": "Kashif",
                "email": "kashif@example.com",
                "requested_time_text": "next Monday",
            },
        ),
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST, confidence=0.40),
        processed=_processed("I might want to book a consultation sometime."),
        state=ThreadState(),
    )
    assert not result.allowed


def test_blocks_counterfactual_consultation_booking() -> None:
    """'If I wanted to book a consultation…' must not trigger a booking action."""
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.SCHEDULE_CONSULTATION,
            ActionStatus.READY,
            collected_slots={
                "name": "Author",
                "email": "author@example.com",
                "requested_time_text": "next Monday",
            },
        ),
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST, confidence=0.85),
        processed=_processed_counterfactual(
            "If I wanted to book a consultation, how would that work?"
        ),
        state=ThreadState(),
    )
    assert not result.allowed
    assert "counterfactual" in result.reason


def test_blocks_negated_nda_generation() -> None:
    """'I don't need an NDA' must block generate_nda even if plan is READY."""
    result = _gate.evaluate(
        action_plan=_plan(ActionType.GENERATE_NDA),
        intent=_intent(query=QueryIntentType.NDA_REQUEST),
        processed=_processed("I don't need an NDA."),
        state=ThreadState(),
    )
    assert not result.allowed
    assert result.blocked_message is not None


def test_allows_real_nda_request_when_slots_present() -> None:
    """Genuine NDA request with all required contact slots must be allowed (or confirm)."""
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.GENERATE_NDA,
            collected_slots={
                "name": "Maya Author",
                "email": "maya@example.com",
                "phone": "555-1234",
                "effective_date": "2026-06-01",
            },
            confirmation_required=True,
        ),
        intent=_intent(query=QueryIntentType.NDA_REQUEST),
        processed=_processed("Please prepare an NDA."),
        state=ThreadState(),
    )
    # Governance allows it; confirmation flag is surfaced via requires_confirmation.
    assert result.allowed or result.requires_confirmation


def test_blocks_negated_agreement_generation() -> None:
    """'I am not ready for agreement' must block generate_agreement."""
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.GENERATE_AGREEMENT,
            collected_slots={"quote_id": "q-1"},
        ),
        intent=_intent(query=QueryIntentType.AGREEMENT_REQUEST),
        processed=_processed("I am not ready for agreement."),
        state=_state_with_quote("q-1"),
    )
    assert not result.allowed


def test_blocks_agreement_without_approved_quote() -> None:
    """Agreement action is blocked when state has no approved/current quote."""
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.GENERATE_AGREEMENT,
            collected_slots={
                "name": "Author",
                "email": "author@example.com",
                "phone": "555-0000",
                "client_location": "Chicago, IL",
                "effective_date": "2026-06-01",
            },
        ),
        intent=_intent(query=QueryIntentType.AGREEMENT_REQUEST),
        processed=_processed("I am ready to sign the agreement."),
        state=ThreadState(),  # no quote_id in state
    )
    assert not result.allowed
    assert result.blocked_message is not None


def test_allows_confirmed_consultation_with_required_slots() -> None:
    """Consultation READY with name/email/time and high confidence must be allowed."""
    result = _gate.evaluate(
        action_plan=_plan(
            ActionType.SCHEDULE_CONSULTATION,
            ActionStatus.READY,
            collected_slots={
                "name": "Kashif",
                "email": "kashif@example.com",
                "requested_time_text": "next Monday at 10am",
                "duration_minutes": 30,
                "business_timezone": "America/Chicago",
            },
            confirmation_required=True,
        ),
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST, confidence=0.92),
        processed=_processed("Yes, book the consultation."),
        state=ThreadState(),
    )
    assert result.allowed
    assert result.idempotency_key is not None


def test_idempotency_key_is_stable_for_same_write_action() -> None:
    """The same action plan and context must always produce the same idempotency_key."""
    plan = _plan(
        ActionType.SCHEDULE_CONSULTATION,
        ActionStatus.READY,
        collected_slots={"name": "Kashif", "email": "kashif@example.com"},
    )
    intent = _intent(query=QueryIntentType.CONSULTATION_REQUEST, confidence=0.92)
    processed = _processed("Yes, book the consultation.")
    state = ThreadState()

    r1 = _gate.evaluate(action_plan=plan, intent=intent, processed=processed, state=state)
    r2 = _gate.evaluate(action_plan=plan, intent=intent, processed=processed, state=state)

    assert r1.idempotency_key is not None
    assert r1.idempotency_key == r2.idempotency_key
