"""Tests for specific bug-fixes applied to bookcraft.components.trimatch.engine.

File organisation
-----------------
TestConfidenceAggregation — Fix: confidence must reflect the winning target's share
                            of the total score, not the raw max rule confidence.
TestTRGArbitration        — Fix: TRG context suppression must remove declined-service
                            evidence without affecting unrelated evidence.
"""

import re

import pytest

from bookcraft.components.preprocessor.schemas import ProcessedMessage, TokenInfo
from bookcraft.components.trimatch import (
    RulePack,
    TriMatchEngine,
    TriMatchMode,
)
from bookcraft.components.trg.schemas import ServiceShiftEvent, TRGContext
from bookcraft.domain.enums import ServiceCategory


# ---------------------------------------------------------------------------
# Shared helper — mirrors the _processed() helper in test_trimatch_engine.py
# ---------------------------------------------------------------------------


def _processed(text: str) -> ProcessedMessage:
    tokens: list[TokenInfo] = []
    for match in re.finditer(r"\b[\w']+\b", text):
        word = match.group(0)
        tokens.append(
            TokenInfo(text=word, lemma=word.casefold(), start=match.start(), end=match.end())
        )
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=tokens,
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[1.0],
        language="en",
        char_count=len(text),
    )


# ---------------------------------------------------------------------------
# TestConfidenceAggregation
#
# Bug: `result.confidence` was previously set to the raw rule confidence of the
# winning rule (e.g. 0.9), instead of the winning target's proportional share of
# the total aggregate score across all targets.
# Fix: confidence = winning_score / total_score, capped to [0, 1].
# ---------------------------------------------------------------------------


class TestConfidenceAggregation:
    def _two_service_pack(self) -> RulePack:
        """A minimal rule pack where two services match the same text."""
        return RulePack.model_validate(
            {
                "version": "confidence-test",
                "rules": [
                    {
                        "id": "SERVICE-GHOST-EX-CFG-001",
                        "layer": "exact",
                        "target": {"service_intent": "ghostwriting"},
                        "phrases": ["ghostwriting"],
                        "confidence": 0.9,
                    },
                    {
                        "id": "SERVICE-EDIT-EX-CFG-002",
                        "layer": "exact",
                        "target": {"service_intent": "editing_proofreading"},
                        "phrases": ["editing"],
                        "confidence": 0.7,
                    },
                ],
            }
        )

    def test_confidence_reflects_winning_target(self) -> None:
        """When two services match, confidence must be the winner's score share.

        ghostwriting score = 0.9, editing_proofreading score = 0.7
        Total = 1.6; ghostwriting share ≈ 0.5625.
        The raw rule confidence (0.9) must NOT appear directly as the result.
        """
        engine = TriMatchEngine(rule_pack=self._two_service_pack(), mode=TriMatchMode.SHADOW)
        result = engine.classify(_processed("I need ghostwriting and editing"))

        # Confidence must be the proportional share, not the raw max.
        assert result.confidence < 0.9, (
            f"Expected confidence < 0.9 (proportional share), got {result.confidence}"
        )
        # Winning service must still have the largest share.
        assert result.confidence > 0.5, (
            f"Expected confidence > 0.5 (ghostwriting is dominant), got {result.confidence}"
        )

    def test_confidence_one_match_full_score(self) -> None:
        """When only one target matches, its share is 100% — confidence ≈ 1.0."""
        rule_pack = RulePack.model_validate(
            {
                "version": "single-match-test",
                "rules": [
                    {
                        "id": "SERVICE-COVER-EX-001",
                        "layer": "exact",
                        "target": {"service_intent": "cover_design_illustration"},
                        "phrases": ["cover design"],
                        "confidence": 0.85,
                    }
                ],
            }
        )
        engine = TriMatchEngine(rule_pack=rule_pack, mode=TriMatchMode.SHADOW)
        result = engine.classify(_processed("I need cover design"))

        # Only one target contributes to total_score, so winning_score / total_score = 1.0.
        assert result.confidence == pytest.approx(1.0, abs=0.01), (
            f"Single-match confidence should be ~1.0, got {result.confidence}"
        )

    def test_confidence_range_valid(self) -> None:
        """Confidence must always be in [0.0, 1.0] regardless of rule configuration."""
        engine = TriMatchEngine(rule_pack=self._two_service_pack(), mode=TriMatchMode.SHADOW)
        result = engine.classify(_processed("I need ghostwriting and editing"))

        assert 0.0 <= result.confidence <= 1.0, (
            f"Confidence out of range: {result.confidence}"
        )


# ---------------------------------------------------------------------------
# TestTRGArbitration
#
# Bug: the TRG arbitration layer was either not wired into classify() or was
# not actually removing declined-service evidence from the final result.
# Fix: _apply_trg_arbitration filters SERVICE_INTENT evidence for services
# present in trg_context.service_shifts with mode="switch".
# ---------------------------------------------------------------------------


class TestTRGArbitration:
    def _ghostwriting_rule_pack(self) -> RulePack:
        return RulePack.model_validate(
            {
                "version": "trg-arbitration-test",
                "rules": [
                    {
                        "id": "SERVICE-GHOST-RX-TRG-001",
                        "layer": "regex",
                        "target": {"service_intent": "ghostwriting"},
                        "regex": r"\bghostwriting\b",
                        "confidence": 0.95,
                    },
                    {
                        "id": "SERVICE-EDIT-EX-TRG-002",
                        "layer": "exact",
                        "target": {"service_intent": "editing_proofreading"},
                        "phrases": ["editing"],
                        "confidence": 0.88,
                    },
                ],
            }
        )

    def test_none_context_no_change(self) -> None:
        """Passing trg_context=None must produce the same result as no context.

        This verifies backward compatibility — existing callers that omit the
        context argument are unaffected by the arbitration path.
        """
        engine = TriMatchEngine(rule_pack=self._ghostwriting_rule_pack(), mode=TriMatchMode.SHADOW)
        message = _processed("I need ghostwriting and editing")

        result_no_ctx = engine.classify(message)
        result_none_ctx = engine.classify(message, trg_context=None)

        assert result_no_ctx.service_primary == result_none_ctx.service_primary
        assert result_no_ctx.confidence == result_none_ctx.confidence

    def test_declined_service_suppressed(self) -> None:
        """A service the user explicitly switched away from must not appear in evidence.

        When a TRGContext records a 'switch' shift away from 'ghostwriting',
        any SERVICE_INTENT evidence for ghostwriting must be removed.
        """
        engine = TriMatchEngine(rule_pack=self._ghostwriting_rule_pack(), mode=TriMatchMode.SHADOW)

        declined_shift = ServiceShiftEvent(
            previous_service="ghostwriting",
            new_service="editing_proofreading",
            mode="switch",
        )
        trg_context = TRGContext(service_shifts=[declined_shift])

        result = engine.classify(_processed("I need ghostwriting and editing"), trg_context=trg_context)

        # Ghostwriting was declined — no evidence or primary result for it.
        assert all(
            item.target != ServiceCategory.GHOSTWRITING.value for item in result.evidence
        ), "Declined ghostwriting evidence must be suppressed by TRG arbitration."
        assert result.service_primary != ServiceCategory.GHOSTWRITING, (
            "Declined service must not be selected as service_primary."
        )

    def test_empty_shifts_no_suppression(self) -> None:
        """An empty TRGContext (no shifts, no forbidden reasks) must not change results.

        A default-constructed TRGContext with no signals is semantically identical
        to no context — the engine should produce the same classification.
        """
        engine = TriMatchEngine(rule_pack=self._ghostwriting_rule_pack(), mode=TriMatchMode.SHADOW)
        message = _processed("I need ghostwriting and editing")

        result_no_ctx = engine.classify(message)
        result_empty_ctx = engine.classify(message, trg_context=TRGContext())

        assert result_no_ctx.service_primary == result_empty_ctx.service_primary
