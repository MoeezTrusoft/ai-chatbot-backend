from __future__ import annotations

from bookcraft.components.context.delegation import DelegatedDecisionDetector
from bookcraft.components.context.schemas import ContextPack

_detector = DelegatedDecisionDetector()


# ---------------------------------------------------------------------------
# 1. "you decide" → delegated for the current slot
# ---------------------------------------------------------------------------


def test_detects_you_decide_as_delegated_for_current_slot() -> None:
    d = _detector.detect(text="You decide.", current_slot="cover_style")
    assert d.detected is True
    assert d.status == "delegated"
    assert d.target_slot == "cover_style"
    assert d.confidence >= 0.85


# ---------------------------------------------------------------------------
# 2. "no idea" → unknown_by_user
# ---------------------------------------------------------------------------


def test_detects_no_idea_as_unknown_by_user() -> None:
    d = _detector.detect(text="I have no idea about that.")
    assert d.detected is True
    assert d.status == "unknown_by_user"
    assert d.confidence >= 0.80


# ---------------------------------------------------------------------------
# 3. "skip that" → declined
# ---------------------------------------------------------------------------


def test_detects_skip_that_as_declined() -> None:
    d = _detector.detect(text="Skip that question, please.", current_slot="genre")
    assert d.detected is True
    assert d.status == "declined"
    assert d.target_slot == "genre"


# ---------------------------------------------------------------------------
# 4. "no deadline" → not_applicable for deadline
# ---------------------------------------------------------------------------


def test_detects_no_deadline_as_not_applicable_for_deadline() -> None:
    d = _detector.detect(text="There's no deadline for this project.")
    assert d.detected is True
    assert d.status == "not_applicable"
    assert d.target_slot == "deadline"


# ---------------------------------------------------------------------------
# 5. Binds to response_plan_next_question when no current_slot
# ---------------------------------------------------------------------------


def test_binds_to_response_plan_next_question() -> None:
    d = _detector.detect(
        text="I don't know.",
        response_plan_next_question="manuscript_stage",
    )
    assert d.detected is True
    assert d.target_slot == "manuscript_stage"


# ---------------------------------------------------------------------------
# 6. Infers cover_style from text when no slot hint provided
# ---------------------------------------------------------------------------


def test_infers_cover_style_from_text() -> None:
    d = _detector.detect(text="I have no idea about the cover style.")
    assert d.detected is True
    assert d.target_slot == "cover_style"


# ---------------------------------------------------------------------------
# 7. No signal returns not_delegated
# ---------------------------------------------------------------------------


def test_explicit_slot_in_text_overrides_current_slot() -> None:
    """The slot the user explicitly names wins over the planner's assumed current_slot.
    "I don't know the cover style, you guys decide" while the planner was about to ask for
    word count must bind to cover_style — not word_or_page_count."""
    d = _detector.detect(
        text="I don't know the cover style, you guys decide.",
        current_slot="word_or_page_count",
        response_plan_next_question="word_or_page_count",
    )
    assert d.detected is True
    assert d.target_slot == "cover_style"


def test_no_signal_returns_not_delegated() -> None:
    d = _detector.detect(text="I need ghostwriting for a 50,000-word fantasy novel.")
    assert d.detected is False
    assert d.status == "not_delegated"
    assert d.confidence == 0.0


# ---------------------------------------------------------------------------
# Extra: context_pack fallback binding
# ---------------------------------------------------------------------------


def test_binds_to_context_pack_allowed_next_questions() -> None:
    pack = ContextPack(allowed_next_questions=["word_or_page_count", "genre"])
    d = _detector.detect(text="I don't know.", context_pack=pack)
    assert d.detected is True
    assert d.target_slot == "word_or_page_count"


def test_binds_to_context_pack_missing_facts_when_no_allowed() -> None:
    pack = ContextPack(missing_facts=["deadline", "genre"])
    d = _detector.detect(text="I don't know.", context_pack=pack)
    assert d.detected is True
    assert d.target_slot == "deadline"


def test_audit_populated_on_detection() -> None:
    d = _detector.detect(text="You decide.", current_slot="genre")
    assert d.audit
    assert any("delegated" in a for a in d.audit)
