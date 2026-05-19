from __future__ import annotations

from bookcraft.components.preprocessor.negation_targets import (
    NegationTargetResolution,
    NegationTargetResolver,
)
from bookcraft.components.preprocessor.schemas import Span


def _resolver() -> NegationTargetResolver:
    return NegationTargetResolver()


def _span(start: int, end: int, text: str, cue: str = "if") -> Span:
    return Span(start=start, end=end, text=text, cue=cue)


def _negated_targets(res: NegationTargetResolution) -> list[str]:
    return [t.target for t in res.targets if t.polarity == "negated"]


def _affirmed_targets(res: NegationTargetResolution) -> list[str]:
    return [t.target for t in res.targets if t.polarity in ("affirmed", "replacement")]


def _targets_by_type(res: NegationTargetResolution, target_type: str) -> list[str]:
    return [t.target for t in res.targets if t.target_type == target_type]


# ---------------------------------------------------------------------------
# 1. Service negation + affirmation
# ---------------------------------------------------------------------------


def test_negates_ghostwriting_and_affirms_editing() -> None:
    res = _resolver().resolve(text="I don't need ghostwriting, I need editing.")
    neg = _negated_targets(res)
    aff = _affirmed_targets(res)
    assert "ghostwriting" in neg, f"Expected ghostwriting negated, got neg={neg}"
    assert "editing_proofreading" in aff, f"Expected editing_proofreading affirmed, got aff={aff}"


# ---------------------------------------------------------------------------
# 2. Document negation + document affirmation
# ---------------------------------------------------------------------------


def test_negates_nda_and_affirms_agreement() -> None:
    res = _resolver().resolve(text="I don't need an NDA, but I do need an agreement.")
    neg = _negated_targets(res)
    aff = _affirmed_targets(res)
    assert any(t in neg for t in ("generate_nda", "nda")), f"Expected NDA negated, got {neg}"
    assert any(t in aff for t in ("generate_agreement", "agreement")), (
        f"Expected agreement affirmed, got {aff}"
    )


# ---------------------------------------------------------------------------
# 3. Tool-action negation (pricing) + affirmation (samples)
# ---------------------------------------------------------------------------


def test_negates_pricing_and_affirms_samples() -> None:
    res = _resolver().resolve(text="Don't send pricing yet, just show me samples.")
    neg = _negated_targets(res)
    aff = _affirmed_targets(res)
    assert "price_quote" in neg, f"Expected price_quote negated, got {neg}"
    assert "portfolio_lookup" in aff, f"Expected portfolio_lookup affirmed, got {aff}"


# ---------------------------------------------------------------------------
# 4. Service negation (cover design) + service affirmation (formatting)
# ---------------------------------------------------------------------------


def test_negates_cover_design_and_affirms_formatting() -> None:
    res = _resolver().resolve(text="No cover design, only formatting.")
    neg = _negated_targets(res)
    aff = _affirmed_targets(res)
    assert "cover_design_illustration" in neg, (
        f"Expected cover_design_illustration negated, got {neg}"
    )
    assert "interior_formatting" in aff, f"Expected interior_formatting affirmed, got {aff}"


# ---------------------------------------------------------------------------
# 5. Project negation (other book)
# ---------------------------------------------------------------------------


def test_project_negation_other_book() -> None:
    res = _resolver().resolve(text="Not this book, my other one.")
    neg = _negated_targets(res)
    aff = _affirmed_targets(res)
    assert "active_project" in neg, f"Expected active_project negated, got {neg}"
    assert "other_project" in aff, f"Expected other_project affirmed, got {aff}"


# ---------------------------------------------------------------------------
# 6. Consultation negation (not now / timing)
# ---------------------------------------------------------------------------


def test_negates_consultation_not_now() -> None:
    res = _resolver().resolve(text="Not a consultation right now, just show me some samples.")
    neg = _negated_targets(res)
    aff = _affirmed_targets(res)
    assert "schedule_consultation" in neg, f"Expected schedule_consultation negated, got {neg}"
    assert "portfolio_lookup" in aff, f"Expected portfolio_lookup affirmed, got {aff}"


# ---------------------------------------------------------------------------
# 7. No false positives without negation
# ---------------------------------------------------------------------------


def test_no_false_positive_without_negation() -> None:
    res = _resolver().resolve(text="I need ghostwriting and editing.")
    assert res.targets == [], f"Expected no targets, got {res.targets}"


# ---------------------------------------------------------------------------
# 8. Counterfactual does NOT affirm the action
# ---------------------------------------------------------------------------


def test_counterfactual_does_not_affirm_action() -> None:
    text = "I don't need ghostwriting, I need editing."
    # Simulate counterfactual span covering the affirmed part ("I need editing.")
    cf_start = text.index("I need editing")
    cf_end = len(text)
    cf_spans = [_span(cf_start, cf_end, text[cf_start:cf_end], cue="if")]

    res = _resolver().resolve(
        text=text,
        counterfactual_spans=cf_spans,
    )
    neg = _negated_targets(res)
    aff = _affirmed_targets(res)
    assert "ghostwriting" in neg, f"Expected ghostwriting negated, got {neg}"
    assert "editing_proofreading" not in aff, (
        f"editing_proofreading should NOT be affirmed (counterfactual), got aff={aff}"
    )


# ---------------------------------------------------------------------------
# Extra: polarity labelling (replacement vs affirmed)
# ---------------------------------------------------------------------------


def test_replacement_polarity_when_same_type() -> None:
    res = _resolver().resolve(text="No cover design, only formatting.")
    replace_targets = [t for t in res.targets if t.polarity == "replacement"]
    targets_found = [t.target for t in replace_targets]
    assert any(t.target == "interior_formatting" for t in replace_targets), (
        f"interior_formatting should be marked as replacement, got {targets_found}"
    )


def test_confidence_is_positive() -> None:
    res = _resolver().resolve(text="I don't need ghostwriting, I need editing.")
    for t in res.targets:
        assert t.confidence > 0, f"confidence must be positive, got {t.confidence}"


def test_audit_is_populated() -> None:
    res = _resolver().resolve(text="I don't need ghostwriting, I need editing.")
    assert res.audit, "audit list should not be empty"
