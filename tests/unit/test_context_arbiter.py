from __future__ import annotations

from bookcraft.components.intent.context_arbiter import ContextArbiter
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span
from bookcraft.domain.enums import (
    ManuscriptStatus,
    QueryIntentType,
    SalesStage,
    ServiceCategory,
    Source,
)
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ServiceInterest, ThreadState

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _intent(
    *,
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: ServiceCategory | None = None,
    service_secondary: list[ServiceCategory] | None = None,
    confidence: float = 0.9,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        service_secondary=service_secondary or [],
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=True,
        confidence=confidence,
        rationale="test",
        evidence=[],
    )


def _processed(
    text: str,
    *,
    services: list[str] | None = None,
    negation_spans: list[Span] | None = None,
) -> ProcessedMessage:
    atoms: dict[str, object] = {}
    if services is not None:
        atoms["services"] = services
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=[],
        negation_spans=negation_spans or [],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms=atoms,
        embedding=[1.0],
        language="en",
        char_count=len(text),
    )


def _state_with_service(service: ServiceCategory) -> ThreadState:
    state = ThreadState()
    state.project.services_discussed.append(
        ServiceInterest(
            service=FieldMeta(
                value=service,
                confidence=0.94,
                source=Source.USER_STATED,
                extracted_by="test",
                raw_excerpt=service.value,
            ),
            confidence=0.94,
        )
    )
    return state


_arbiter = ContextArbiter()


# ===========================================================================
# Required tests (exact names from spec)
# ===========================================================================


def test_retains_active_service_when_current_turn_has_no_explicit_service() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.GHOSTWRITING)
    # No "services" key in atoms → no explicit service signal in this turn.
    processed = _processed("Its fiction children book as I told you.")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.COVER_DESIGN_ILLUSTRATION
    assert result.intent.service_secondary == []
    assert any("state_service_inertia" in c for c in result.corrections)


def test_allows_explicit_service_switch() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.EDITING_PROOFREADING)
    processed = _processed(
        "Actually I need editing instead.",
        services=["editing_proofreading"],
    )

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.EDITING_PROOFREADING
    assert any("explicit_service_switch" in tok for tok in result.corrections + result.audit)


def test_additive_service_request_keeps_existing_and_adds_new() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.MARKETING_PROMOTION)
    processed = _processed(
        "Can you also help with marketing?",
        services=["marketing_promotion"],
    )

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    # cover design must not be erased
    assert result.intent.service_primary != ServiceCategory.MARKETING_PROMOTION
    # marketing must appear in secondary OR audit signals an additive addition
    assert ServiceCategory.MARKETING_PROMOTION in result.intent.service_secondary or any(
        "service_addition" in a or "additive" in a for a in result.audit
    )


def test_negated_pricing_does_not_remain_pricing() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.PRICING_QUESTION)
    # "Don't send a quote" — prefix-window negation suppresses the pricing match.
    processed = _processed("Don't send a quote yet.")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary != QueryIntentType.PRICING_QUESTION
    assert any("pricing_negation_veto" in a for a in result.audit)


def test_real_pricing_still_allowed() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.PRICING_QUESTION)
    processed = _processed("Can you give me a quote for ghostwriting?")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary == QueryIntentType.PRICING_QUESTION


def test_negated_nda_does_not_remain_nda_request() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.NDA_REQUEST)
    processed = _processed("I don't need an NDA.")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary != QueryIntentType.NDA_REQUEST


def test_negated_agreement_does_not_remain_agreement_request() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.AGREEMENT_REQUEST)
    processed = _processed("I am not ready for agreement.")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary != QueryIntentType.AGREEMENT_REQUEST


# ===========================================================================
# Additional coverage (broader inertia, additive, document, audit)
# ===========================================================================


def test_service_inertia_blocks_weak_drift() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.GHOSTWRITING)
    processed = _processed("what do you recommend for my book?")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.COVER_DESIGN_ILLUSTRATION
    assert ServiceCategory.GHOSTWRITING not in result.intent.service_secondary
    assert "state_service_inertia" in result.intent.evidence
    assert any("state_service_inertia" in c for c in result.corrections)


def test_service_inertia_preserves_evidence_tag() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.GHOSTWRITING)
    processed = _processed("what do you think?")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert "state_service_inertia" in result.intent.evidence


def test_no_inertia_when_no_active_service() -> None:
    state = ThreadState()
    intent = _intent(service=ServiceCategory.GHOSTWRITING)
    processed = _processed("I need ghostwriting help", services=["ghostwriting"])

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.GHOSTWRITING
    assert not result.corrections
    assert any("no_active_service" in a for a in result.audit)


def test_unanchored_inferred_service_is_cleared() -> None:
    """BUG-6060: a bare genre/premise description on an unanchored thread must NOT pivot
    to an inferred service. "cozy mystery with magic and food" names no service and has
    no deterministic cue, yet the LLM ensemble infers GHOSTWRITING. The arbiter clears it
    so the reply stays neutral instead of pivoting to ghostwriting (and downstream never
    stamps a durable 0.94 ghostwriting focus)."""
    state = ThreadState()  # no active service (e.g. landing anchor missing)
    intent = _intent(service=ServiceCategory.GHOSTWRITING)  # evidence=[] → pure inference
    processed = _processed("cozy mystery with magic and food")  # no "services" atom

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary is None
    assert result.intent.service_secondary == []
    assert any("unanchored_inferred_service_cleared" in c for c in result.corrections)


def test_unanchored_explicit_service_is_kept() -> None:
    """A service the visitor explicitly named is kept even with no active thread focus."""
    state = ThreadState()
    intent = _intent(service=ServiceCategory.GHOSTWRITING)
    processed = _processed("I need ghostwriting help", services=["ghostwriting"])

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.GHOSTWRITING


def test_unanchored_deterministically_supported_service_is_kept() -> None:
    """A service set by the deterministic hardening layer (keyword match) is kept even
    without an active focus — only bare LLM inferences are cleared."""
    state = ThreadState()
    intent = _intent(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = intent.model_copy(
        update={"evidence": ["deterministic_service_signal:cover_design_illustration"]}
    )
    processed = _processed("I want a book cover")  # no atoms, but hardening-backed

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.COVER_DESIGN_ILLUSTRATION


def test_inertia_noop_when_intent_already_matches_active() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    processed = _processed("looks good so far")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.COVER_DESIGN_ILLUSTRATION
    assert not result.corrections


def test_explicit_switch_allows_service_change() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.EDITING_PROOFREADING)
    processed = _processed(
        "Actually I need editing instead",
        services=["editing_proofreading"],
    )

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.EDITING_PROOFREADING
    assert any("explicit_service_switch" in a for a in result.audit)


def test_explicit_switch_with_forget_phrasing() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.INTERIOR_FORMATTING)
    processed = _processed(
        "Forget cover design, I need formatting",
        services=["interior_formatting"],
    )

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.INTERIOR_FORMATTING
    assert any("explicit_service_switch" in a for a in result.audit)


def test_additive_request_preserves_primary_adds_secondary() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    intent = _intent(service=ServiceCategory.INTERIOR_FORMATTING)
    processed = _processed(
        "Can you also help with formatting?",
        services=["interior_formatting"],
    )

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.COVER_DESIGN_ILLUSTRATION
    assert ServiceCategory.INTERIOR_FORMATTING in result.intent.service_secondary
    assert any("additive" in c for c in result.corrections)


def test_additive_as_well_phrasing() -> None:
    state = _state_with_service(ServiceCategory.GHOSTWRITING)
    intent = _intent(service=ServiceCategory.EDITING_PROOFREADING)
    processed = _processed(
        "I need editing as well",
        services=["editing_proofreading"],
    )

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.service_primary == ServiceCategory.GHOSTWRITING
    assert ServiceCategory.EDITING_PROOFREADING in result.intent.service_secondary


def test_negated_pricing_intent_is_vetoed() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.PRICING_QUESTION)
    processed = _processed("I don't need a quote right now")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary != QueryIntentType.PRICING_QUESTION
    assert any("pricing_vetoed" in c for c in result.corrections)
    assert any("pricing_negation_veto" in a for a in result.audit)


def test_real_pricing_intent_is_preserved() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.PRICING_QUESTION)
    processed = _processed("Can you give me a quote for ghostwriting?")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary == QueryIntentType.PRICING_QUESTION
    assert not any("pricing_vetoed" in c for c in result.corrections)


def test_direct_cost_question_preserved() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.PRICING_QUESTION)
    processed = _processed("How much does editing cost?")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary == QueryIntentType.PRICING_QUESTION


def test_negated_nda_request_is_vetoed() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.NDA_REQUEST)
    processed = _processed("I don't need an NDA right now")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary != QueryIntentType.NDA_REQUEST
    assert any("nda_request_vetoed" in c for c in result.corrections)


def test_valid_nda_request_is_preserved() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.NDA_REQUEST)
    processed = _processed("Please send me an NDA before I share the manuscript")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary == QueryIntentType.NDA_REQUEST
    assert not any("nda_request_vetoed" in c for c in result.corrections)


def test_negated_agreement_request_is_vetoed() -> None:
    state = ThreadState()
    intent = _intent(query=QueryIntentType.AGREEMENT_REQUEST)
    processed = _processed("I don't want to sign an agreement yet")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert result.intent.query_primary != QueryIntentType.AGREEMENT_REQUEST
    assert any("agreement_request_vetoed" in c for c in result.corrections)


def test_known_genre_appears_in_audit() -> None:
    state = ThreadState()
    state.project.genre = FieldMeta(
        value="children's fiction",
        confidence=0.9,
        source=Source.USER_STATED,
        extracted_by="test",
        raw_excerpt="children's fiction",
    )
    intent = _intent()
    processed = _processed("what do you think?")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert any("genre:children's fiction" in a for a in result.audit)


def test_known_manuscript_status_appears_in_audit() -> None:
    state = ThreadState()
    state.project.manuscript_status = FieldMeta(
        value=ManuscriptStatus.COMPLETED_DRAFT,
        confidence=0.9,
        source=Source.USER_STATED,
        extracted_by="test",
        raw_excerpt="completed draft",
    )
    intent = _intent()
    processed = _processed("I have finished my manuscript.")

    result = _arbiter.arbitrate(intent=intent, processed=processed, state=state)

    assert any("manuscript_status" in a for a in result.audit)
