from __future__ import annotations

from typing import Any

from bookcraft.services.chat import _trimatch_tiebreaker_considered_payload


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


class FakeIntentVote:
    def __init__(
        self,
        *,
        query_primary: str = "service_question",
        service_primary: str | None = None,
        funnel_stage: str = "service_discovery",
        confidence: float = 0.6,
    ) -> None:
        self.query_primary = query_primary
        self.service_primary = service_primary
        self.funnel_stage = funnel_stage
        self.confidence = confidence
        self.evidence: list[str] = []
        self.needs_clarification = False


def test_tiebreaker_eligibility_can_be_true_but_applied_stays_false() -> None:
    payload = _payload(
        active_trimatch=FakeTriMatchResult(service_primary="cover_design_illustration"),
        extra_tiebreaker=FakeTriMatchResult(service_primary="editing_proofreading"),
        ensemble_intent=FakeIntentVote(service_primary="cover_design_illustration"),
        final_intent=FakeIntentVote(service_primary="cover_design_illustration"),
    )

    assert payload["decision"]["eligible"] is True
    assert payload["decision"]["applied"] is False
    assert payload["decision"]["dimension"] == "service_primary"
    assert payload["decision"]["recommended_value"] == "editing_proofreading"
    assert payload["safety"]["side_effects_allowed"] is False


def test_tiebreaker_blocks_sensitive_pricing_recommendation() -> None:
    payload = _payload(
        active_trimatch=FakeTriMatchResult(query_primary="service_question"),
        extra_tiebreaker=FakeTriMatchResult(query_primary="pricing_question"),
        ensemble_intent=FakeIntentVote(query_primary="service_question"),
        final_intent=FakeIntentVote(query_primary="service_question"),
    )

    assert payload["decision"]["eligible"] is False
    assert payload["decision"]["applied"] is False
    assert "safety-sensitive intent cannot use tiebreaker" in payload["decision"]["blocked_reasons"]
    assert "forbidden recommended value: pricing_question" in payload["decision"]["blocked_reasons"]
    assert payload["safety"]["pricing_sensitive"] is True


def test_tiebreaker_blocks_sensitive_document_recommendation() -> None:
    payload = _payload(
        active_trimatch=FakeTriMatchResult(query_primary="service_question"),
        extra_tiebreaker=FakeTriMatchResult(query_primary="agreement_request"),
        ensemble_intent=FakeIntentVote(query_primary="service_question"),
        final_intent=FakeIntentVote(query_primary="service_question"),
    )

    assert payload["decision"]["eligible"] is False
    assert payload["decision"]["applied"] is False
    assert payload["safety"]["document_sensitive"] is True


def test_tiebreaker_blocks_when_recommendation_matches_final() -> None:
    payload = _payload(
        active_trimatch=FakeTriMatchResult(service_primary="editing_proofreading"),
        extra_tiebreaker=FakeTriMatchResult(service_primary="editing_proofreading"),
        ensemble_intent=FakeIntentVote(service_primary="editing_proofreading"),
        final_intent=FakeIntentVote(service_primary="editing_proofreading"),
    )

    assert payload["decision"]["eligible"] is False
    assert payload["decision"]["applied"] is False
    assert "recommendation already matches final intent" in payload["decision"]["blocked_reasons"]


def test_tiebreaker_blocks_when_final_confidence_is_high() -> None:
    payload = _payload(
        active_trimatch=FakeTriMatchResult(service_primary="cover_design_illustration"),
        extra_tiebreaker=FakeTriMatchResult(service_primary="editing_proofreading"),
        ensemble_intent=FakeIntentVote(service_primary="cover_design_illustration"),
        final_intent=FakeIntentVote(
            service_primary="cover_design_illustration",
            confidence=0.95,
        ),
    )

    assert payload["decision"]["eligible"] is False
    assert payload["decision"]["applied"] is False
    assert "final confidence above tiebreaker threshold" in payload["decision"]["blocked_reasons"]


def _payload(
    *,
    active_trimatch: FakeTriMatchResult | None,
    extra_tiebreaker: FakeTriMatchResult,
    ensemble_intent: FakeIntentVote,
    final_intent: FakeIntentVote,
) -> dict[str, Any]:
    return _trimatch_tiebreaker_considered_payload(
        active_trimatch=active_trimatch,
        extra_tiebreaker=extra_tiebreaker,
        ensemble_intent=ensemble_intent,
        final_intent=final_intent,
    )
