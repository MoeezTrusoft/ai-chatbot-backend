from __future__ import annotations

from typing import Any

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.trimatch.schemas import TriMatchDimension, TriMatchLayer
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.services.chat import (
    _apply_shortcut_to_intent,
    _trimatch_shortcut_considered_payload,
)


class FakeEvidence:
    def __init__(
        self,
        *,
        rule_id: str = "shortcut_rule_001",
        dimension: str = "service_intent",
        target: str = "editing_proofreading",
        layer: str = "exact",
        shortcut_eligible: bool = True,
        negated: bool = False,
        counterfactual: bool = False,
    ) -> None:
        self.rule_id = rule_id
        self.dimension = TriMatchDimension(dimension)
        self.target = target
        self.layer = TriMatchLayer(layer)
        self.matched_text = target
        self.confidence = 0.99
        self.negated = negated
        self.hedged = False
        self.counterfactual = counterfactual
        self.shortcut_eligible = shortcut_eligible

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "rule_id": self.rule_id,
            "dimension": self.dimension.value,
            "target": self.target,
            "layer": self.layer.value,
            "matched_text": self.matched_text,
            "confidence": self.confidence,
            "negated": self.negated,
            "hedged": self.hedged,
            "counterfactual": self.counterfactual,
            "shortcut_eligible": self.shortcut_eligible,
        }


class FakeShortcutResult:
    def __init__(
        self,
        *,
        query_primary: str | None = None,
        service_primary: str | None = None,
        evidence: list[FakeEvidence] | None = None,
    ) -> None:
        self.query_primary = query_primary
        self.service_primary = service_primary
        self.funnel_stage = None
        self.confidence = 0.99
        self.evidence = evidence or []
        self.shortcut_eligible = bool(self.evidence)


def test_safe_exact_service_shortcut_applies_without_side_effects() -> None:
    intent = _intent(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    payload = _payload(
        extra_shortcut=FakeShortcutResult(
            service_primary="editing_proofreading",
            evidence=[
                FakeEvidence(
                    dimension="service_intent",
                    target="editing_proofreading",
                    layer="exact",
                    shortcut_eligible=True,
                )
            ],
        ),
        final_intent=intent,
    )

    updated = _apply_shortcut_to_intent(
        intent=intent,
        shortcut=payload["shortcut"],
    )

    assert payload["shortcut"]["eligible"] is True
    assert payload["shortcut"]["applied"] is True
    assert payload["shortcut"]["dimension"] == "service_primary"
    assert payload["shortcut"]["recommended_value"] == "editing_proofreading"
    assert payload["shortcut"]["rule_id"] == "shortcut_rule_001"
    assert payload["safety"]["side_effects_allowed"] is False
    assert updated.service_primary == ServiceCategory.EDITING_PROOFREADING
    assert updated.query_primary == QueryIntentType.SERVICE_QUESTION
    assert "trimatch shortcut applied" in updated.evidence[-1]


def test_safe_regex_query_shortcut_applies_without_side_effects() -> None:
    intent = _intent(query_primary=QueryIntentType.UNCLEAR)
    payload = _payload(
        extra_shortcut=FakeShortcutResult(
            query_primary="service_question",
            evidence=[
                FakeEvidence(
                    rule_id="shortcut_rule_regex_001",
                    dimension="query_intent",
                    target="service_question",
                    layer="regex",
                    shortcut_eligible=True,
                )
            ],
        ),
        final_intent=intent,
    )

    updated = _apply_shortcut_to_intent(
        intent=intent,
        shortcut=payload["shortcut"],
    )

    assert payload["shortcut"]["eligible"] is True
    assert payload["shortcut"]["applied"] is True
    assert payload["shortcut"]["dimension"] == "query_primary"
    assert payload["shortcut"]["recommended_value"] == "service_question"
    assert payload["safety"]["side_effects_allowed"] is False
    assert updated.query_primary == QueryIntentType.SERVICE_QUESTION


def test_pricing_shortcut_cannot_apply() -> None:
    intent = _intent(query_primary=QueryIntentType.SERVICE_QUESTION)
    payload = _payload(
        extra_shortcut=FakeShortcutResult(
            query_primary="pricing_question",
            evidence=[
                FakeEvidence(
                    dimension="query_intent",
                    target="pricing_question",
                    layer="exact",
                    shortcut_eligible=True,
                )
            ],
        ),
        final_intent=intent,
    )

    updated = _apply_shortcut_to_intent(intent=intent, shortcut=payload["shortcut"])

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert payload["safety"]["pricing_sensitive"] is True
    assert payload["safety"]["side_effects_allowed"] is False
    assert updated == intent


def test_semantic_shortcut_cannot_apply() -> None:
    intent = _intent(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    payload = _payload(
        extra_shortcut=FakeShortcutResult(
            service_primary="editing_proofreading",
            evidence=[
                FakeEvidence(
                    dimension="service_intent",
                    target="editing_proofreading",
                    layer="semantic",
                    shortcut_eligible=False,
                )
            ],
        ),
        final_intent=intent,
    )

    updated = _apply_shortcut_to_intent(intent=intent, shortcut=payload["shortcut"])

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert "semantic or fuzzy evidence cannot shortcut" in payload["shortcut"]["blocked_reasons"]
    assert updated == intent


def test_negated_shortcut_cannot_apply() -> None:
    intent = _intent(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    payload = _payload(
        extra_shortcut=FakeShortcutResult(
            service_primary="editing_proofreading",
            evidence=[
                FakeEvidence(
                    dimension="service_intent",
                    target="editing_proofreading",
                    layer="exact",
                    shortcut_eligible=True,
                    negated=True,
                )
            ],
        ),
        final_intent=intent,
    )

    updated = _apply_shortcut_to_intent(intent=intent, shortcut=payload["shortcut"])

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert "negated evidence cannot shortcut" in payload["shortcut"]["blocked_reasons"]
    assert updated == intent


def test_missing_rule_id_cannot_apply() -> None:
    intent = _intent(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    payload = _payload(
        extra_shortcut=FakeShortcutResult(
            service_primary="editing_proofreading",
            evidence=[
                FakeEvidence(
                    rule_id="",
                    dimension="service_intent",
                    target="editing_proofreading",
                    layer="exact",
                    shortcut_eligible=True,
                )
            ],
        ),
        final_intent=intent,
    )

    updated = _apply_shortcut_to_intent(intent=intent, shortcut=payload["shortcut"])

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert "missing shortcut rule_id" in payload["shortcut"]["blocked_reasons"]
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
        rationale="Shortcut application test intent.",
        evidence=[],
    )


def _payload(
    *,
    extra_shortcut: FakeShortcutResult,
    final_intent: IntentVote,
) -> dict[str, Any]:
    return _trimatch_shortcut_considered_payload(
        extra_shortcut=extra_shortcut,
        final_intent=final_intent,
    )
