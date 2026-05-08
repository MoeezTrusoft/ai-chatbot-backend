import re

import pytest
from pydantic import ValidationError

from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span, TokenInfo
from bookcraft.components.trimatch import (
    RulePack,
    RuleRepository,
    TriMatchDimension,
    TriMatchEngine,
    TriMatchLayer,
    TriMatchMode,
    TriMatchRule,
    TriMatchVerifier,
    load_eval_examples,
)
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory


def _processed(
    text: str,
    *,
    negation_spans: list[Span] | None = None,
    hedge_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> ProcessedMessage:
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
        negation_spans=negation_spans or [],
        hedge_spans=hedge_spans or [],
        counterfactual_spans=counterfactual_spans or [],
        deterministic_atoms={},
        embedding=[1.0],
        language="en",
        char_count=len(text),
    )


def _engine(mode: TriMatchMode = TriMatchMode.SHADOW) -> TriMatchEngine:
    return TriMatchEngine(
        rule_pack=RuleRepository("data/trimatch/rules").load_active_rules(),
        mode=mode,
        shortcut_layers={TriMatchLayer.EXACT, TriMatchLayer.REGEX, TriMatchLayer.PATTERN},
        shortcut_threshold=0.97,
        funnel_stage_weight=0.0,
    )


def test_trimatch_classifies_query_service_and_funnel_stage_shadow() -> None:
    result = _engine().classify(_processed("pricing quote how much does ghostwriting cost"))

    assert result.query_primary == QueryIntentType.PRICING_QUESTION
    assert result.service_primary == ServiceCategory.GHOSTWRITING
    assert result.funnel_stage is None
    assert result.mode == TriMatchMode.SHADOW


def test_trimatch_funnel_stage_is_shadow_only_when_detected() -> None:
    result = _engine().classify(_processed("quote requested send proposal"))

    assert result.funnel_stage == SalesStage.QUOTE_REQUESTED
    assert TriMatchDimension.FUNNEL_STAGE in result.shadow_only_dimensions


def test_shortcut_enabled_uses_only_allowed_layers() -> None:
    result = _engine(TriMatchMode.SHORTCUT_ENABLED).classify(
        _processed("pricing quote how much does ghostwriting cost")
    )

    assert result.shortcut_eligible is True
    assert any(item.shortcut_eligible for item in result.evidence)


def test_negation_suppresses_matching_evidence() -> None:
    text = "I do not need audiobook"
    start = text.index("not")
    result = _engine().classify(
        _processed(
            text,
            negation_spans=[Span(start=start, end=len(text), text=text[start:], cue="not")],
        )
    )

    assert result.service_primary is None
    assert all(item.target != "audiobook_production" for item in result.evidence)


def test_hedge_damps_evidence_but_keeps_signal() -> None:
    text = "I might need ghostwriting"
    start = text.index("might")
    result = _engine().classify(
        _processed(
            text,
            hedge_spans=[Span(start=start, end=len(text), text=text[start:], cue="might")],
        )
    )

    assert result.service_primary == ServiceCategory.GHOSTWRITING
    ghost_evidence = [item for item in result.evidence if item.target == "ghostwriting"]
    assert ghost_evidence
    assert max(item.confidence for item in ghost_evidence) < 0.98


def test_counterfactual_suppresses_matching_evidence() -> None:
    text = "If I needed cover design later"
    result = _engine().classify(
        _processed(
            text,
            counterfactual_spans=[
                Span(start=0, end=len(text), text=text, cue="if"),
            ],
        )
    )

    assert result.service_primary is None


def test_schema_rejects_invalid_funnel_stage_target() -> None:
    with pytest.raises(ValidationError):
        TriMatchRule.model_validate(
            {
                "id": "BAD-FS",
                "layer": "exact",
                "target": {"funnel_stage": "proposal"},
                "phrases": ["proposal"],
            }
        )


def test_verifier_rejects_funnel_pricing_or_legal_rules() -> None:
    rule_pack = RulePack.model_validate(
        {
            "version": "bad",
            "rules": [
                {
                    "id": "BAD-FS-PRICE",
                    "layer": "exact",
                    "target": {"funnel_stage": "quote_requested"},
                    "phrases": ["price ready"],
                }
            ],
        }
    )

    result = TriMatchVerifier().verify(rule_pack, [])

    assert result.valid is False
    assert result.errors


def test_trimatch_verifier_accepts_seed_rules_and_eval() -> None:
    rule_pack = RuleRepository("data/trimatch/rules").load_active_rules()
    result = TriMatchVerifier().verify(rule_pack, load_eval_examples("data/trimatch/eval"))

    assert result.valid is True
    assert result.precision
    assert result.recall
