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


def test_safe_service_tiebreaker_applies_but_side_effects_stay_disabled() -> None:
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


def test_pricing_tiebreaker_is_not_applied() -> None:
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


def test_document_tiebreaker_is_not_applied() -> None:
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
        rationale="Test intent.",
        evidence=[],
    )
