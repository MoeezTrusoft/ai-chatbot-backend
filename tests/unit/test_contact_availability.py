"""Tests for the tri-state contact status (given / not_given / unavailable).

Covers chat 6759: the customer said their phone was unusable and email-only, yet
the bot kept demanding a phone number and never let the consultation proceed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bookcraft.components.leads.contact_availability import ContactAvailabilityDetector
from bookcraft.components.sales.consultation_objective import ConsultationObjectiveEngine
from bookcraft.components.sales.consultation_state import reduce_consultation_state
from bookcraft.components.sales.current_question_priority import CurrentQuestionPriorityResult
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState


# --------------------------------------------------------------------------- #
# Detector
# --------------------------------------------------------------------------- #


def test_detects_phone_unavailable_from_chat_6759_phrase() -> None:
    d = ContactAvailabilityDetector()
    r = d.detect(
        "unfortunately currently my phone is unable to be used. "
        "my main source of contact is my email"
    )
    assert r.phone_unavailable is True
    assert r.email_unavailable is False


def test_various_phone_unavailable_phrasings() -> None:
    d = ContactAvailabilityDetector()
    for msg in (
        "I don't have a phone",
        "no phone number",
        "my cell is not working",
        "email is my only contact",
        "I prefer email",
        "my phone is disconnected",
        "cannot take calls",
    ):
        assert d.detect(msg).phone_unavailable is True, msg


def test_does_not_false_positive_on_real_phone_or_neutral() -> None:
    d = ContactAvailabilityDetector()
    for msg in (
        "my phone is 6099604230",
        "I will give you my phone later",
        "call me at 2:30",
        "I want to publish my book",
    ):
        r = d.detect(msg)
        assert r.phone_unavailable is False, msg
        assert r.email_unavailable is False, msg


# --------------------------------------------------------------------------- #
# Consultation reducer honours phone_unavailable
# --------------------------------------------------------------------------- #


def _intent(primary: QueryIntentType = QueryIntentType.CONSULTATION_REQUEST) -> MagicMock:
    m = MagicMock()
    m.query_primary = primary
    return m


def test_reducer_blocks_scheduling_without_phone_by_default() -> None:
    """Baseline: phone required, none given, NOT flagged unavailable → keep asking."""
    decision = reduce_consultation_state(
        state=ThreadState(),
        message="I'd like to book a call, my name is Marisol, marisol@example.com",
        intent=_intent(),
        contact_ready=True,
        has_email=True,
        has_phone=False,
        require_phone=True,
        phone_unavailable=False,
    )
    assert decision.next_question == "missing_phone"
    assert decision.can_schedule is False


def test_reducer_proceeds_email_only_when_phone_unavailable() -> None:
    """chat 6759: phone flagged unavailable → skip the phone gate, ask for time."""
    decision = reduce_consultation_state(
        state=ThreadState(),
        message="I'd like to book a call",
        intent=_intent(),
        contact_ready=True,
        has_email=True,
        has_phone=False,
        require_phone=True,
        phone_unavailable=True,
    )
    # No longer stuck demanding a phone — advances to the time question instead.
    assert decision.next_question != "missing_phone"
    assert "phone_unavailable_proceed_email_only" in " ".join(decision.audit)


def test_reducer_reaches_ready_to_schedule_email_only() -> None:
    """Full email-only path with a definite time + known timezone → can_schedule."""
    state = ThreadState()
    state.preferred_timezone = "America/New_York"
    state.preferred_call_time = "Friday at 3pm"
    decision = reduce_consultation_state(
        state=state,
        message="Friday at 3pm works",
        intent=_intent(),
        contact_ready=True,
        has_email=True,
        has_phone=False,
        require_phone=True,
        phone_unavailable=True,
    )
    assert decision.can_schedule is True
    assert str(decision.stage) == "ready_to_schedule"


# --------------------------------------------------------------------------- #
# Consultation objective engine honours phone_unavailable
# --------------------------------------------------------------------------- #


def _contact(ready: bool = True, has_phone: bool = False) -> MagicMock:
    m = MagicMock()
    m.lead_contact_ready = ready
    m.has_phone = has_phone
    return m


def _lod(move: str = "no_change") -> MagicMock:
    m = MagicMock()
    m.objective_move = move
    m.stage = "engaging"
    return m


def _priority(has_priority: bool = False) -> CurrentQuestionPriorityResult:
    return CurrentQuestionPriorityResult(
        has_priority=has_priority, question_type=None, should_answer_before_capture=False
    )


def test_objective_engine_stops_asking_phone_when_unavailable() -> None:
    engine = ConsultationObjectiveEngine()
    state = ThreadState()
    state.lead_created = True
    decision = engine.decide(
        message="I'd like to speak with someone",
        state=state,
        lead_objective_decision=_lod(),
        contact_capture=_contact(ready=True, has_phone=False),
        current_question_priority=_priority(),
        require_phone=True,
        phone_unavailable=True,
    )
    assert decision.next_question != "missing_phone"
    assert decision.objective_move != "ask_preferred_call_time" or decision.stage != (
        "consultation_phone_requested"
    )


def test_objective_engine_books_email_only_with_definite_time() -> None:
    engine = ConsultationObjectiveEngine()
    state = ThreadState()
    state.lead_created = True
    state.preferred_call_time = "Monday at 2:30pm"
    decision = engine.decide(
        message="Monday at 2:30pm works",
        state=state,
        lead_objective_decision=_lod(),
        contact_capture=_contact(ready=True, has_phone=False),
        current_question_priority=_priority(),
        require_phone=True,
        phone_unavailable=True,
    )
    assert decision.create_handoff is True


def test_objective_engine_still_asks_phone_when_not_unavailable() -> None:
    engine = ConsultationObjectiveEngine()
    state = ThreadState()
    state.lead_created = True
    decision = engine.decide(
        message="I'd like to speak with someone",
        state=state,
        lead_objective_decision=_lod(),
        contact_capture=_contact(ready=True, has_phone=False),
        current_question_priority=_priority(),
        require_phone=True,
        phone_unavailable=False,
    )
    assert decision.next_question == "missing_phone"
