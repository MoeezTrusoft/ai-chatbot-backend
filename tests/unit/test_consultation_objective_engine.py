"""Tests for ConsultationObjectiveEngine."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bookcraft.components.sales.consultation_objective import ConsultationObjectiveEngine
from bookcraft.components.sales.current_question_priority import CurrentQuestionPriorityResult
from bookcraft.domain.state import ThreadState


@pytest.fixture
def engine() -> ConsultationObjectiveEngine:
    return ConsultationObjectiveEngine()


def _state(
    *,
    lead_created: bool = False,
    preferred_call_time: str | None = None,
    consultation_stage: str | None = None,
) -> ThreadState:
    s = ThreadState()
    s.lead_created = lead_created
    s.preferred_call_time = preferred_call_time
    s.consultation_stage = consultation_stage
    return s


def _contact_ready(ready: bool = True) -> MagicMock:
    m = MagicMock()
    m.lead_contact_ready = ready
    return m


def _lod(move: str = "continue_light_discovery", stage: str = "engaging") -> MagicMock:
    m = MagicMock()
    m.objective_move = move
    m.stage = stage
    m.stop_discovery = move != "continue_light_discovery"
    m.recommended_primary_goal = "lead_contact_capture" if move == "ask_contact" else None
    m.next_question = "name_and_email_or_phone" if move == "ask_contact" else None
    return m


def _priority(has_priority: bool = False, qt: str | None = None) -> CurrentQuestionPriorityResult:
    return CurrentQuestionPriorityResult(
        has_priority=has_priority,
        question_type=qt,
        should_answer_before_capture=has_priority,
        suppress_old_sales_path=qt in {"topic_correction", "contact_refusal"},
    )


def test_contact_ready_asks_preferred_call_time(engine: ConsultationObjectiveEngine) -> None:
    """Contact ready (lead confirmed from PRIOR turn) + no call time → ask for call time."""
    decision = engine.decide(
        message="I'd like to speak with someone.",
        # lead_created=True AND lod_move != "create_lead" → prior turn confirmed
        state=_state(lead_created=True),
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact_ready(True),
    )
    assert decision.ask_preferred_time is True
    assert decision.next_question == "preferred_call_time"
    assert decision.stage == "consultation_time_requested"


def test_contact_and_definite_time_create_handoff(engine: ConsultationObjectiveEngine) -> None:
    """Contact ready + DEFINITE call time (day + clock) → create handoff."""
    decision = engine.decide(
        message="Let's do Friday at 3pm",
        state=_state(lead_created=True, preferred_call_time="Friday at 3pm"),
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact_ready(True),
    )
    assert decision.create_handoff is True
    assert decision.stage == "consultation_pending"
    assert decision.recommended_primary_goal == "consultation_handoff_confirmation"


def test_contact_and_indefinite_time_offers_slots(engine: ConsultationObjectiveEngine) -> None:
    """Contact ready + INDEFINITE call time → offer concrete slots, not a handoff."""
    decision = engine.decide(
        message="Let's do Friday afternoon",
        state=_state(lead_created=True, preferred_call_time="Friday afternoon"),
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact_ready(True),
    )
    assert decision.create_handoff is False
    assert decision.objective_move == "ask_preferred_call_time"
    assert decision.next_question == "preferred_call_time_slots"
    assert decision.recommended_primary_goal == "consultation_time_capture"


def test_definite_call_time_extracted_in_same_turn(engine: ConsultationObjectiveEngine) -> None:
    """Definite call time provided after lead confirmation → extract and proceed to handoff."""
    decision = engine.decide(
        message="Available tomorrow at 2pm",
        # lead_created=True AND lod_move="no_change" → lead confirmed prior turn
        state=_state(lead_created=True),
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact_ready(True),
    )
    assert decision.create_handoff is True
    assert decision.extracted_preferred_call_time is not None
    assert "tomorrow" in (decision.extracted_preferred_call_time or "").lower()


def test_indefinite_call_time_extracted_offers_slots(engine: ConsultationObjectiveEngine) -> None:
    """Vague call time provided this turn → store it but offer concrete slots."""
    decision = engine.decide(
        message="Available tomorrow afternoon",
        state=_state(lead_created=True),
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact_ready(True),
    )
    assert decision.create_handoff is False
    assert decision.next_question == "preferred_call_time_slots"
    assert decision.extracted_preferred_call_time is not None
    assert "tomorrow" in (decision.extracted_preferred_call_time or "").lower()


def _contact(*, ready=True, has_email=True, has_phone=False) -> MagicMock:
    m = MagicMock()
    m.lead_contact_ready = ready
    m.has_email = has_email
    m.has_phone = has_phone
    return m


def test_email_only_contact_asks_phone_before_scheduling(
    engine: ConsultationObjectiveEngine,
) -> None:
    """require_phone + email-only + not yet asked → ask phone before pivoting to call time."""
    decision = engine.decide(
        message="ok",
        state=_state(lead_created=True),  # consultation_phone_asked defaults False
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact(has_email=True, has_phone=False),
        require_phone=True,
    )
    assert decision.next_question == "missing_phone"
    assert decision.recommended_primary_goal == "consultation_time_capture"
    assert decision.stage == "consultation_phone_requested"


def test_phone_gate_is_loop_safe_after_asked(engine: ConsultationObjectiveEngine) -> None:
    """Once asked, never block — proceed to call-time even if phone still missing."""
    s = _state(lead_created=True)
    s.consultation_phone_asked = True
    decision = engine.decide(
        message="ok",
        state=s,
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact(has_email=True, has_phone=False),
        require_phone=True,
    )
    assert decision.next_question != "missing_phone"
    assert decision.objective_move == "ask_preferred_call_time"


def test_phone_gate_skipped_when_phone_present(engine: ConsultationObjectiveEngine) -> None:
    decision = engine.decide(
        message="ok",
        state=_state(lead_created=True),
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact(has_email=True, has_phone=True),
        require_phone=True,
    )
    assert decision.next_question != "missing_phone"


def test_phone_gate_off_when_require_phone_false(engine: ConsultationObjectiveEngine) -> None:
    decision = engine.decide(
        message="ok",
        state=_state(lead_created=True),
        lead_objective_decision=_lod("no_change"),
        contact_capture=_contact(has_email=True, has_phone=False),
        require_phone=False,
    )
    assert decision.next_question != "missing_phone"


def test_current_question_priority_answers_first(engine: ConsultationObjectiveEngine) -> None:
    """When user asks pricing, engine says answer_then_consultation first."""
    decision = engine.decide(
        message="How much does ghostwriting cost?",
        state=_state(),
        lead_objective_decision=_lod("ask_contact"),
        contact_capture=_contact_ready(False),
        current_question_priority=_priority(True, "pricing"),
    )
    assert decision.objective_move == "answer_then_consultation"
    assert decision.recommended_primary_goal == "answer_current_question"
    assert decision.ask_contact is False


def test_contact_refusal_does_not_push_contact(engine: ConsultationObjectiveEngine) -> None:
    """Contact refusal → don't push contact capture."""
    decision = engine.decide(
        message="I don't want to share my contact before knowing the price.",
        state=_state(),
        lead_objective_decision=_lod("ask_contact"),
        contact_capture=_contact_ready(False),
        current_question_priority=_priority(True, "contact_refusal"),
    )
    assert decision.ask_contact is False
    assert decision.objective_move == "answer_then_consultation"


def test_no_contact_ready_asks_contact_when_user_ready(engine: ConsultationObjectiveEngine) -> None:
    """Lead objective asks contact → engine honours it."""
    decision = engine.decide(
        message="I'm ready to proceed.",
        state=_state(),
        lead_objective_decision=_lod("ask_contact"),
        contact_capture=_contact_ready(False),
        current_question_priority=_priority(False),
    )
    assert decision.ask_contact is True
    assert decision.next_question == "name_and_email_or_phone"


def test_greeting_continues_conversation(engine: ConsultationObjectiveEngine) -> None:
    """Plain greeting → continue conversation."""
    decision = engine.decide(
        message="Hello there!",
        state=_state(),
        lead_objective_decision=_lod("continue_light_discovery"),
        contact_capture=_contact_ready(False),
        current_question_priority=_priority(False),
    )
    assert decision.objective_move == "continue_conversation"
    assert decision.stop_discovery is False


def test_topic_correction_suppresses_old_path(engine: ConsultationObjectiveEngine) -> None:
    """Topic correction → suppress old path and answer corrected topic."""
    decision = engine.decide(
        message="I was asking about distribution, not ghostwriting.",
        state=_state(),
        lead_objective_decision=_lod("ask_contact"),
        contact_capture=_contact_ready(False),
        current_question_priority=_priority(True, "topic_correction"),
    )
    assert decision.objective_move == "answer_then_consultation"
    assert decision.recommended_primary_goal == "answer_current_question"


def test_audit_populated(engine: ConsultationObjectiveEngine) -> None:
    decision = engine.decide(
        message="Hello",
        state=_state(),
        lead_objective_decision=_lod(),
    )
    assert decision.audit
