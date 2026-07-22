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
    # v2 rule army reads the explicit "quote" cue as the quote_requested funnel stage
    # (v1 had no funnel coverage here). Funnel remains a shadow-only dimension.
    assert result.funnel_stage == SalesStage.QUOTE_REQUESTED
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


def test_exact_match_does_not_match_inside_other_words() -> None:
    result = _engine().classify(_processed("Which publishing platforms do you support?"))

    assert result.query_primary == QueryIntentType.PUBLISHING_PLATFORM_QUESTION
    assert all(item.target != QueryIntentType.GREETING.value for item in result.evidence)


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
    # The active gate verifies the ACTIVE (seed/v1) rule pack against the eval that
    # matches it (v1). The v2 rules-army eval lives with its own staged army under
    # data/trimatch/staged/rules_army_v2/ — it must NOT be dropped into this active
    # dir (doing so graded v1 rules against unpromoted v2-army eval; see the v2
    # PROMOTION_STATUS for the calibration gap that gates that promotion).
    rule_pack = RuleRepository("data/trimatch/rules").load_active_rules()
    result = TriMatchVerifier().verify(rule_pack, load_eval_examples("data/trimatch/eval"))

    assert result.valid is True
    assert result.precision
    assert result.recall


def test_context_arbitration_suppresses_create_book_when_book_trailer_matches() -> None:
    rule_pack = RulePack.model_validate(
        {
            "version": "context-arbitration-test",
            "rules": [
                {
                    "id": "SERVICE-GHOST-RX-038",
                    "layer": "regex",
                    "target": {"service_intent": "ghostwriting"},
                    "regex": r"\bcreate a book\b",
                    "confidence": 0.955,
                },
                {
                    "id": "SERVICE-TRAILER-EX-002",
                    "layer": "exact",
                    "target": {"service_intent": "video_trailer"},
                    "phrases": ["book trailer"],
                    "confidence": 0.985,
                },
            ],
        }
    )

    engine = TriMatchEngine(rule_pack=rule_pack, mode=TriMatchMode.SHADOW)
    result = engine.classify(_processed("Can you create a book trailer for Instagram and YouTube?"))

    assert result.service_primary == ServiceCategory.VIDEO_TRAILER
    assert all(item.target != ServiceCategory.GHOSTWRITING.value for item in result.evidence)


def test_context_arbitration_suppresses_help_opener_greeting() -> None:
    rule_pack = RulePack.model_validate(
        {
            "version": "context-arbitration-test",
            "rules": [
                {
                    "id": "QUERY-GREETING-PT-016",
                    "layer": "pattern",
                    "target": {"query_intent": "greeting"},
                    "pattern": ["i", "need", "help"],
                    "confidence": 0.93,
                }
            ],
        }
    )

    engine = TriMatchEngine(rule_pack=rule_pack, mode=TriMatchMode.SHADOW)
    result = engine.classify(_processed("I need help with a memoir."))

    assert result.query_primary is None
    assert all(item.target != QueryIntentType.GREETING.value for item in result.evidence)


def test_context_arbitration_keeps_greeting_only_message() -> None:
    rule_pack = RulePack.model_validate(
        {
            "version": "context-arbitration-test",
            "rules": [
                {
                    "id": "QUERY-GREETING-EX-001",
                    "layer": "exact",
                    "target": {"query_intent": "greeting"},
                    "phrases": ["hello"],
                    "confidence": 0.985,
                }
            ],
        }
    )

    engine = TriMatchEngine(rule_pack=rule_pack, mode=TriMatchMode.SHADOW)
    result = engine.classify(_processed("hello"))

    assert result.query_primary == QueryIntentType.GREETING


def test_context_arbitration_suppresses_simple_terms_agreement() -> None:
    rule_pack = RulePack.model_validate(
        {
            "version": "context-arbitration-test",
            "rules": [
                {
                    "id": "QUERY-AGREE-EX-007",
                    "layer": "exact",
                    "target": {"query_intent": "agreement_request"},
                    "phrases": ["terms"],
                    "confidence": 0.965,
                }
            ],
        }
    )

    engine = TriMatchEngine(rule_pack=rule_pack, mode=TriMatchMode.SHADOW)
    result = engine.classify(_processed("Can you explain BookCraft services in simple terms?"))

    assert result.query_primary is None
    assert all(item.target != QueryIntentType.AGREEMENT_REQUEST.value for item in result.evidence)


def test_trimatch_preserves_secondary_services_in_score_order() -> None:
    rule_pack = RulePack.model_validate(
        {
            "version": "secondary-services-test",
            "rules": [
                {
                    "id": "SERVICE-EDIT-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "editing_proofreading"},
                    "phrases": ["editing"],
                    "confidence": 0.98,
                },
                {
                    "id": "SERVICE-FORMAT-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "interior_formatting"},
                    "phrases": ["formatting"],
                    "confidence": 0.97,
                },
                {
                    "id": "SERVICE-MKT-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "marketing_promotion"},
                    "phrases": ["marketing"],
                    "confidence": 0.96,
                },
            ],
        }
    )

    engine = TriMatchEngine(rule_pack=rule_pack, mode=TriMatchMode.SHADOW)
    result = engine.classify(_processed("I need editing, formatting, and marketing."))

    assert result.service_primary == ServiceCategory.EDITING_PROOFREADING
    assert result.service_secondary == [
        ServiceCategory.INTERIOR_FORMATTING,
        ServiceCategory.MARKETING_PROMOTION,
    ]


def test_trimatch_secondary_services_exclude_negated_service() -> None:
    rule_pack = RulePack.model_validate(
        {
            "version": "secondary-services-negation-test",
            "rules": [
                {
                    "id": "SERVICE-GHOST-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "ghostwriting"},
                    "phrases": ["ghostwriting"],
                    "confidence": 0.99,
                },
                {
                    "id": "SERVICE-EDIT-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "editing_proofreading"},
                    "phrases": ["editing"],
                    "confidence": 0.98,
                },
                {
                    "id": "SERVICE-FORMAT-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "interior_formatting"},
                    "phrases": ["formatting"],
                    "confidence": 0.97,
                },
            ],
        }
    )

    text = "I need editing and formatting, but no ghostwriting."
    start = text.index("no")
    engine = TriMatchEngine(rule_pack=rule_pack, mode=TriMatchMode.SHADOW)
    result = engine.classify(
        _processed(
            text,
            negation_spans=[Span(start=start, end=len(text), text=text[start:], cue="no")],
        )
    )

    assert result.service_primary == ServiceCategory.EDITING_PROOFREADING
    assert result.service_secondary == [ServiceCategory.INTERIOR_FORMATTING]
    assert all(item.target != ServiceCategory.GHOSTWRITING.value for item in result.evidence)


def test_trimatch_uses_preprocessor_service_order_when_available() -> None:
    rule_pack = RulePack.model_validate(
        {
            "version": "atom-service-order-test",
            "rules": [
                {
                    "id": "SERVICE-FORMAT-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "interior_formatting"},
                    "phrases": ["formatting"],
                    "confidence": 0.99,
                },
                {
                    "id": "SERVICE-EDIT-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "editing_proofreading"},
                    "phrases": ["editing"],
                    "confidence": 0.98,
                },
            ],
        }
    )

    processed = _processed("I need editing and formatting.")
    processed = processed.model_copy(
        update={
            "deterministic_atoms": {"services": ["editing_proofreading", "interior_formatting"]}
        }
    )

    engine = TriMatchEngine(rule_pack=rule_pack, mode=TriMatchMode.SHADOW)
    result = engine.classify(processed)

    assert result.service_primary == ServiceCategory.EDITING_PROOFREADING
    assert result.service_secondary == [ServiceCategory.INTERIOR_FORMATTING]
