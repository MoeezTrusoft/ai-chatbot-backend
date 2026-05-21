"""Tests for ContextEnforcementGate."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bookcraft.components.context.enforcement import ContextEnforcementGate
from bookcraft.domain.state import ThreadState


@pytest.fixture
def gate() -> ContextEnforcementGate:
    return ContextEnforcementGate()


def _state(**kwargs) -> ThreadState:
    s = ThreadState()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _intent(query: str = "service_question", service: str | None = None) -> MagicMock:
    m = MagicMock()
    m.query_primary = query
    m.service_primary = MagicMock()
    m.service_primary.value = service or ""
    return m


def _cover_pack() -> MagicMock:
    m = MagicMock()
    m.active_service = "cover_design_illustration"
    m.allowed_next_questions = ["cover_style"]
    m.missing_facts = ["cover_style"]
    m.contact_capture_status = "missing"
    m.delegated_slots = []
    m.unknown_slots = []
    m.genre_status = None
    return m


# ---------------------------------------------------------------------------
# Part B: Cover style delegation
# ---------------------------------------------------------------------------


def test_delegated_cover_style_suppresses_cover_style_question(
    gate: ContextEnforcementGate,
) -> None:
    """'you guys design it' with cover context → cover_style delegated, forbidden."""
    dec = gate.enforce(
        text="You guys design it for me",
        intent=_intent(service="cover_design_illustration"),
        state=_state(),
        context_pack=_cover_pack(),
    )
    assert "cover_style" in dec.delegated_slots
    assert any("cover_style" in r for r in dec.forbidden_reasks)
    assert any("delegated_slot:cover_style" in a for a in dec.audit)


def test_come_up_with_own_delegates_cover_style(gate: ContextEnforcementGate) -> None:
    dec = gate.enforce(
        text="come up with your own design",
        intent=_intent(service="cover_design_illustration"),
        state=_state(),
        context_pack=_cover_pack(),
    )
    assert "cover_style" in dec.delegated_slots
    assert dec.forced_primary_goal is not None


# ---------------------------------------------------------------------------
# Part C: Unknown/no-idea word count
# ---------------------------------------------------------------------------


def test_repeated_no_idea_word_count_becomes_forbidden_reask(
    gate: ContextEnforcementGate,
) -> None:
    """'again no idea about pages or words' → word_or_page_count forbidden."""
    pack = MagicMock()
    pack.active_service = "cover_design_illustration"
    pack.allowed_next_questions = ["word_or_page_count"]
    pack.missing_facts = ["word_or_page_count"]
    pack.contact_capture_status = "missing"
    pack.delegated_slots = []
    pack.unknown_slots = []

    dec = gate.enforce(
        text="again no idea about pages or words",
        intent=_intent(),
        state=_state(),
        context_pack=pack,
    )
    assert "word_or_page_count" in dec.unknown_slots
    assert "word_or_page_count" in dec.forbidden_reasks
    assert any("repeated_slot_refusal" in a for a in dec.audit)


def test_no_idea_without_count_context_still_captures_unknown(
    gate: ContextEnforcementGate,
) -> None:
    """'no idea' with 'how many words' context → word_or_page_count unknown."""
    dec = gate.enforce(
        text="I have no idea how many words it is",
        intent=_intent(),
        state=_state(),
    )
    assert "word_or_page_count" in dec.unknown_slots


# ---------------------------------------------------------------------------
# Part D: Current question beats stale slot
# ---------------------------------------------------------------------------


def test_publishing_timeline_overrides_cover_style_slot(
    gate: ContextEnforcementGate,
) -> None:
    """Publishing timeline question → answer_current_question, cover_style suppressed."""
    cqp = MagicMock()
    cqp.has_priority = True
    cqp.question_type = "timeline"

    dec = gate.enforce(
        text="How long will it take to publish my book including designing its cover?",
        intent=_intent(),
        state=_state(),
        context_pack=_cover_pack(),
        current_question_priority=cqp,
    )
    assert dec.forced_primary_goal == "answer_current_question"
    assert dec.forced_current_question_type == "timeline"
    assert "cover_style" in dec.forbidden_reasks


def test_current_question_priority_beats_old_missing_slot(
    gate: ContextEnforcementGate,
) -> None:
    """Distribution question → answer_current_question, old ghostwriting slot suppressed."""
    cqp = MagicMock()
    cqp.has_priority = True
    cqp.question_type = "distribution"

    pack = MagicMock()
    pack.active_service = "ghostwriting"
    pack.allowed_next_questions = ["word_or_page_count"]
    pack.missing_facts = ["word_or_page_count"]
    pack.contact_capture_status = "missing"
    pack.delegated_slots = []
    pack.unknown_slots = []

    dec = gate.enforce(
        text="how does distribution work",
        intent=_intent(),
        state=_state(),
        context_pack=pack,
        current_question_priority=cqp,
    )
    assert dec.forced_primary_goal == "answer_current_question"
    assert "word_or_page_count" in dec.forbidden_reasks


# ---------------------------------------------------------------------------
# Part E: Consultation request
# ---------------------------------------------------------------------------


def test_consultation_request_suppresses_word_count_slot(
    gate: ContextEnforcementGate,
) -> None:
    """Consultation request intent → consult goal, word_count suppressed."""
    dec = gate.enforce(
        text="listen to my story and suggest me",
        intent=_intent(query="consultation_request"),
        state=_state(),
    )
    assert dec.forced_primary_goal in {
        "consultation_offer",
        "contact_capture_for_consultation",
        "consultation_time_capture",
    }
    assert "word_or_page_count" in dec.forbidden_reasks


def test_schedule_a_call_triggers_consultation(gate: ContextEnforcementGate) -> None:
    dec = gate.enforce(
        text="Can I schedule a call with your specialist?",
        intent=_intent(),
        state=_state(),
    )
    assert dec.forced_primary_goal in {
        "consultation_offer",
        "contact_capture_for_consultation",
    }


# ---------------------------------------------------------------------------
# Part G: Negated platforms
# ---------------------------------------------------------------------------


def test_not_amazon_only_ingramspark_removes_amazon(
    gate: ContextEnforcementGate,
) -> None:
    """'I don't want Amazon, only IngramSpark' → amazon_kdp negated."""
    state = _state(publishing_platforms=["amazon_kdp", "ingramspark"])
    dec = gate.enforce(
        text="I don't want Amazon, only IngramSpark.",
        intent=_intent(),
        state=state,
    )
    assert "amazon_kdp" in dec.negated_platforms
    assert "ingramspark" not in dec.negated_platforms
    # State updates should remove amazon_kdp
    updated_platforms = dec.state_updates.get("publishing_platforms")
    if updated_platforms is not None:
        assert "amazon_kdp" not in updated_platforms
        assert "ingramspark" in updated_platforms


# ---------------------------------------------------------------------------
# Part F: Service correction
# ---------------------------------------------------------------------------


def test_not_ghostwriting_switches_to_distribution(
    gate: ContextEnforcementGate,
) -> None:
    """'not ghostwriting, distribution' → ghostwriting negated, distribution active."""
    dec = gate.enforce(
        text="I asked about distribution, not ghostwriting.",
        intent=_intent(),
        state=_state(),
    )
    assert "ghostwriting" in dec.negated_services
    assert dec.forced_primary_goal == "answer_current_question"
    assert any("service_correction" in a for a in dec.audit)


# ---------------------------------------------------------------------------
# Part H: False genre clearing
# ---------------------------------------------------------------------------


def test_weak_autobiography_does_not_confirm_fiction(
    gate: ContextEnforcementGate,
) -> None:
    """autobiography → fiction default cleared, memoir candidate added."""
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    state = _state()
    # Simulate fiction was extracted from "story"
    state.project.genre = FieldMeta[str](
        value="fiction", confidence=0.7, source=Source.AI_EXTRACTED
    )

    dec = gate.enforce(
        text="my autobiography about my life journey",
        intent=_intent(),
        state=state,
    )
    assert "project.genre" in dec.cleared_false_facts
    assert dec.state_updates.get("clear_genre") is True
    assert dec.state_updates.get("genre_candidate") == "memoir"


def test_explicit_fiction_not_cleared(gate: ContextEnforcementGate) -> None:
    """User explicitly says 'fiction' → genre stays confirmed."""
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    state = _state()
    state.project.genre = FieldMeta[str](
        value="fiction", confidence=0.9, source=Source.AI_EXTRACTED
    )
    dec = gate.enforce(
        text="I'm writing a fiction novel about space exploration",
        intent=_intent(),
        state=state,
    )
    # "fiction" in text → should NOT clear it
    assert "project.genre" not in dec.cleared_false_facts


def test_user_stated_genre_not_cleared(gate: ContextEnforcementGate) -> None:
    """User-stated genre is never cleared."""
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    state = _state()
    state.project.genre = FieldMeta[str](value="fiction", confidence=1.0, source=Source.USER_STATED)
    dec = gate.enforce(
        text="my story idea",
        intent=_intent(),
        state=state,
    )
    assert "project.genre" not in dec.cleared_false_facts


def test_no_enforcement_for_clean_message(gate: ContextEnforcementGate) -> None:
    """Clean messages produce no enforcement decisions."""
    dec = gate.enforce(
        text="I need editing for my completed novel.",
        intent=_intent(),
        state=_state(),
    )
    assert dec.forced_primary_goal is None
    assert dec.delegated_slots == []
    assert dec.unknown_slots == []
