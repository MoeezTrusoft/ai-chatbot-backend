from __future__ import annotations

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.services.chat import (
    _apply_tiebreaker_to_intent,
    _trimatch_tiebreaker_considered_payload,
)


class FakeTriMatchResult:
    def __init__(
        self,
        *,
        query_primary: str | None = None,
        service_primary: str | None = None,
        funnel_stage: str | None = None,
        confidence: float = 0.95,
        evidence_count: int = 1,
    ) -> None:
        self.query_primary = query_primary
        self.service_primary = service_primary
        self.funnel_stage = funnel_stage
        self.confidence = confidence
        self.evidence = [object()] * evidence_count
        self.shortcut_eligible = False


def test_safe_service_application_keeps_side_effects_disabled() -> None:
    final_intent = _intent(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION)

    payload = _trimatch_tiebreaker_considered_payload(
        active_trimatch=FakeTriMatchResult(service_primary="cover_design_illustration"),
        extra_tiebreaker=FakeTriMatchResult(service_primary="editing_proofreading"),
        ensemble_intent=_intent(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        final_intent=final_intent,
    )

    updated = _apply_tiebreaker_to_intent(
        intent=final_intent,
        decision=payload["decision"],
    )

    assert payload["decision"]["eligible"] is True
    assert payload["decision"]["applied"] is True
    assert payload["decision"]["dimension"] == "service_primary"
    assert payload["decision"]["recommended_value"] == "editing_proofreading"
    assert payload["safety"]["side_effects_allowed"] is False

    assert updated.service_primary == ServiceCategory.EDITING_PROOFREADING
    assert updated.query_primary == QueryIntentType.SERVICE_QUESTION
    assert updated.funnel_stage == SalesStage.SERVICE_DISCOVERY


def test_safe_query_application_keeps_side_effects_disabled() -> None:
    final_intent = _intent(query_primary=QueryIntentType.UNCLEAR)

    payload = _trimatch_tiebreaker_considered_payload(
        active_trimatch=FakeTriMatchResult(query_primary="unclear"),
        extra_tiebreaker=FakeTriMatchResult(query_primary="service_question"),
        ensemble_intent=_intent(query_primary=QueryIntentType.UNCLEAR),
        final_intent=final_intent,
    )

    updated = _apply_tiebreaker_to_intent(
        intent=final_intent,
        decision=payload["decision"],
    )

    assert payload["decision"]["eligible"] is True
    assert payload["decision"]["applied"] is True
    assert payload["decision"]["dimension"] == "query_primary"
    assert payload["decision"]["recommended_value"] == "service_question"
    assert payload["safety"]["side_effects_allowed"] is False

    assert updated.query_primary == QueryIntentType.SERVICE_QUESTION
    assert updated.service_primary is None


def test_pricing_application_is_blocked() -> None:
    final_intent = _intent(query_primary=QueryIntentType.SERVICE_QUESTION)

    payload = _trimatch_tiebreaker_considered_payload(
        active_trimatch=FakeTriMatchResult(query_primary="service_question"),
        extra_tiebreaker=FakeTriMatchResult(query_primary="pricing_question"),
        ensemble_intent=_intent(query_primary=QueryIntentType.SERVICE_QUESTION),
        final_intent=final_intent,
    )

    updated = _apply_tiebreaker_to_intent(
        intent=final_intent,
        decision=payload["decision"],
    )

    assert payload["decision"]["eligible"] is False
    assert payload["decision"]["applied"] is False
    assert payload["safety"]["pricing_sensitive"] is True
    assert payload["safety"]["side_effects_allowed"] is False
    assert updated == final_intent


def test_document_application_is_blocked() -> None:
    final_intent = _intent(query_primary=QueryIntentType.SERVICE_QUESTION)

    payload = _trimatch_tiebreaker_considered_payload(
        active_trimatch=FakeTriMatchResult(query_primary="service_question"),
        extra_tiebreaker=FakeTriMatchResult(query_primary="agreement_request"),
        ensemble_intent=_intent(query_primary=QueryIntentType.SERVICE_QUESTION),
        final_intent=final_intent,
    )

    updated = _apply_tiebreaker_to_intent(
        intent=final_intent,
        decision=payload["decision"],
    )

    assert payload["decision"]["eligible"] is False
    assert payload["decision"]["applied"] is False
    assert payload["safety"]["document_sensitive"] is True
    assert payload["safety"]["side_effects_allowed"] is False
    assert updated == final_intent


def test_portfolio_application_is_blocked() -> None:
    final_intent = _intent(query_primary=QueryIntentType.SERVICE_QUESTION)

    payload = _trimatch_tiebreaker_considered_payload(
        active_trimatch=FakeTriMatchResult(query_primary="service_question"),
        extra_tiebreaker=FakeTriMatchResult(query_primary="portfolio_request"),
        ensemble_intent=_intent(query_primary=QueryIntentType.SERVICE_QUESTION),
        final_intent=final_intent,
    )

    updated = _apply_tiebreaker_to_intent(
        intent=final_intent,
        decision=payload["decision"],
    )

    assert payload["decision"]["eligible"] is False
    assert payload["decision"]["applied"] is False
    assert payload["safety"]["portfolio_sensitive"] is True
    assert payload["safety"]["side_effects_allowed"] is False
    assert updated == final_intent


def test_unsupported_dimension_cannot_apply() -> None:
    intent = _intent(service_primary=ServiceCategory.EDITING_PROOFREADING)

    updated = _apply_tiebreaker_to_intent(
        intent=intent,
        decision={
            "eligible": True,
            "applied": True,
            "dimension": "funnel_stage",
            "recommended_value": "quoted",
        },
    )

    assert updated == intent


def _intent(
    *,
    query_primary: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service_primary: ServiceCategory | None = None,
) -> IntentVote:
    return IntentVote(
        query_primary=query_primary,
        service_primary=service_primary,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.6,
        rationale="Governance smoke intent.",
        evidence=[],
    )
