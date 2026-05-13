from __future__ import annotations

from typing import Any

from bookcraft.components.trimatch.schemas import TriMatchDimension, TriMatchLayer
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.services.chat import _trimatch_shortcut_considered_payload


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
        funnel_stage: str | None = None,
        evidence: list[FakeEvidence] | None = None,
    ) -> None:
        self.query_primary = query_primary
        self.service_primary = service_primary
        self.funnel_stage = funnel_stage
        self.confidence = 0.99
        self.evidence = evidence or []
        self.shortcut_eligible = bool(self.evidence)


class FakeIntentVote:
    def __init__(
        self,
        *,
        query_primary: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
        service_primary: ServiceCategory | None = None,
        funnel_stage: SalesStage = SalesStage.SERVICE_DISCOVERY,
        confidence: float = 0.6,
    ) -> None:
        self.query_primary = query_primary
        self.service_primary = service_primary
        self.funnel_stage = funnel_stage
        self.confidence = confidence
        self.evidence: list[str] = []
        self.needs_clarification = False


def test_shortcut_eligibility_can_be_true_but_applied_stays_false() -> None:
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
        final_intent=FakeIntentVote(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )

    assert payload["shortcut"]["eligible"] is True
    assert payload["shortcut"]["applied"] is False
    assert payload["shortcut"]["dimension"] == "service_primary"
    assert payload["shortcut"]["recommended_value"] == "editing_proofreading"
    assert payload["shortcut"]["rule_id"] == "shortcut_rule_001"
    assert payload["safety"]["side_effects_allowed"] is False


def test_shortcut_blocks_pricing_recommendation() -> None:
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
        final_intent=FakeIntentVote(query_primary=QueryIntentType.SERVICE_QUESTION),
    )

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert payload["safety"]["pricing_sensitive"] is True
    assert "forbidden recommended value: pricing_question" in payload["shortcut"]["blocked_reasons"]


def test_shortcut_blocks_semantic_evidence() -> None:
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
        final_intent=FakeIntentVote(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert "semantic or fuzzy evidence cannot shortcut" in payload["shortcut"]["blocked_reasons"]


def test_shortcut_blocks_shortcut_allowed_false() -> None:
    payload = _payload(
        extra_shortcut=FakeShortcutResult(
            service_primary="editing_proofreading",
            evidence=[
                FakeEvidence(
                    dimension="service_intent",
                    target="editing_proofreading",
                    layer="exact",
                    shortcut_eligible=False,
                )
            ],
        ),
        final_intent=FakeIntentVote(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert "shortcut_allowed false or missing on evidence" in payload["shortcut"]["blocked_reasons"]


def test_shortcut_blocks_negated_evidence() -> None:
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
        final_intent=FakeIntentVote(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert "negated evidence cannot shortcut" in payload["shortcut"]["blocked_reasons"]


def test_shortcut_blocks_counterfactual_evidence() -> None:
    payload = _payload(
        extra_shortcut=FakeShortcutResult(
            service_primary="editing_proofreading",
            evidence=[
                FakeEvidence(
                    dimension="service_intent",
                    target="editing_proofreading",
                    layer="exact",
                    shortcut_eligible=True,
                    counterfactual=True,
                )
            ],
        ),
        final_intent=FakeIntentVote(service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )

    assert payload["shortcut"]["eligible"] is False
    assert payload["shortcut"]["applied"] is False
    assert "counterfactual evidence cannot shortcut" in payload["shortcut"]["blocked_reasons"]


def _payload(
    *,
    extra_shortcut: FakeShortcutResult,
    final_intent: FakeIntentVote,
) -> dict[str, Any]:
    return _trimatch_shortcut_considered_payload(
        extra_shortcut=extra_shortcut,
        final_intent=final_intent,
    )
