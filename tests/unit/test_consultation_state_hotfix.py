"""Unit tests for consultation_state.py (Phase 5 hotfix)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bookcraft.components.sales.consultation_state import (
    ConsultationStage,
    reduce_consultation_state,
    user_asks_consultation_status,
)
from bookcraft.domain.state import ThreadState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent(primary=None):
    from bookcraft.domain.enums import QueryIntentType

    m = MagicMock()
    m.query_primary = primary or QueryIntentType.SERVICE_QUESTION
    return m


def _consultation_intent():
    from bookcraft.domain.enums import QueryIntentType

    return _intent(QueryIntentType.CONSULTATION_REQUEST)


def _state(
    *,
    preferred_call_time: str | None = None,
    preferred_timezone: str | None = None,
    consultation_stage: str | None = None,
    consultation_requested: bool = False,
    pending_confirmation: bool = False,
    confirmed_appointment_id: str | None = None,
    handoff_created: bool = False,
) -> ThreadState:
    s = ThreadState()
    if preferred_call_time:
        s.preferred_call_time = preferred_call_time
    if preferred_timezone:
        s.preferred_timezone = preferred_timezone
    if consultation_stage:
        s.consultation_stage = consultation_stage
    if consultation_requested:
        s.sales_actions.consultation.requested = True
    if pending_confirmation:
        s.sales_actions.consultation.pending_confirmation = True
    if confirmed_appointment_id:
        s.sales_actions.consultation.confirmed_appointment_id = confirmed_appointment_id
    if handoff_created:
        s.consultation_handoff_created = True
    return s


# ---------------------------------------------------------------------------
# user_asks_consultation_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Have my consultation been scheduled?",
        "Is my consultation confirmed?",
        "When is my call?",
        "Did you book it?",
        "Is it booked?",
        "What time is my consultation?",
        "Has the appointment been scheduled?",
        "consultation status update please",
    ],
)
def test_user_asks_consultation_status_detects_status_questions(text: str) -> None:
    assert user_asks_consultation_status(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "I'd like to book a consultation",
        "Can we schedule a call?",
        "What services do you offer?",
        "How much does ghostwriting cost?",
    ],
)
def test_user_asks_consultation_status_ignores_non_status(text: str) -> None:
    assert user_asks_consultation_status(text) is False


# ---------------------------------------------------------------------------
# reduce_consultation_state — NONE stage
# ---------------------------------------------------------------------------


def test_no_consultation_request_returns_none_stage() -> None:
    s = _state()
    decision = reduce_consultation_state(
        state=s,
        message="Tell me about your editing service.",
        intent=_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.NONE
    assert decision.can_schedule is False
    assert decision.consultation_requested is False


# ---------------------------------------------------------------------------
# reduce_consultation_state — contact needed
# ---------------------------------------------------------------------------


def test_objective_engine_engaging_stage_is_not_a_consultation_request() -> None:
    """Regression (BUG-6060): the ConsultationObjectiveEngine overwrites
    state.consultation_stage with "engaging" on ordinary chat turns. The reducer must
    NOT treat that as a consultation request, or every turn from turn 1 latches into
    REQUESTED_CONTACT_NEEDED.
    """
    s = _state(consultation_stage="engaging")
    decision = reduce_consultation_state(
        state=s,
        message="cozy mystery with magic and food",
        intent=_intent(),  # non-consultation intent
        contact_ready=False,
        prior_stage=None,  # turn 1: nothing persisted yet
    )
    assert decision.stage == ConsultationStage.NONE
    assert decision.consultation_requested is False


def test_prior_active_request_stage_latches_across_turns() -> None:
    """A genuine prior request stage keeps the consultation alive even when the current
    message neither restates the request nor carries a consultation intent."""
    s = _state(consultation_stage="engaging")  # live value already polluted by the engine
    decision = reduce_consultation_state(
        state=s,
        message="sure, go ahead",
        intent=_intent(),  # non-consultation intent this turn
        contact_ready=False,
        prior_stage=ConsultationStage.REQUESTED_CONTACT_NEEDED,
    )
    assert decision.stage == ConsultationStage.REQUESTED_CONTACT_NEEDED
    assert decision.consultation_requested is True


def test_prior_none_stage_does_not_latch() -> None:
    s = _state(consultation_stage="engaging")
    decision = reduce_consultation_state(
        state=s,
        message="tell me about cover design",
        intent=_intent(),
        contact_ready=False,
        prior_stage=ConsultationStage.NONE,
    )
    assert decision.stage == ConsultationStage.NONE
    assert decision.consultation_requested is False


def test_consultation_requested_no_contact_needs_contact() -> None:
    s = _state()
    decision = reduce_consultation_state(
        state=s,
        message="I'd like to book a free consultation.",
        intent=_consultation_intent(),
        contact_ready=False,
    )
    assert decision.stage == ConsultationStage.REQUESTED_CONTACT_NEEDED
    assert decision.contact_ready is False
    assert decision.next_question == "name_and_email_or_phone"
    assert decision.stop_discovery is True


# ---------------------------------------------------------------------------
# reduce_consultation_state — time needed
# ---------------------------------------------------------------------------


def test_contact_ready_no_time_needs_time() -> None:
    s = _state()
    decision = reduce_consultation_state(
        state=s,
        message="I'd like to schedule a consultation.",
        intent=_consultation_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.REQUESTED_TIME_NEEDED
    assert decision.next_question == "preferred_call_time"
    assert decision.stop_discovery is True


# ---------------------------------------------------------------------------
# reduce_consultation_state — timezone needed
# ---------------------------------------------------------------------------


def test_relative_time_window_offers_slots() -> None:
    # "Friday afternoon" is an indefinite window (no specific clock time): rather
    # than silently coercing or asking for timezone, offer concrete half-hour slots.
    s = _state(preferred_call_time="Friday afternoon")
    decision = reduce_consultation_state(
        state=s,
        message="Let's talk Friday afternoon.",
        intent=_consultation_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED
    assert decision.can_schedule is False
    assert decision.next_question == "preferred_call_time_slots"


def test_morning_window_offers_slots() -> None:
    s = _state(preferred_call_time="tomorrow morning")
    decision = reduce_consultation_state(
        state=s,
        message="I'm available tomorrow morning.",
        intent=_consultation_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED
    assert decision.can_schedule is False
    assert decision.next_question == "preferred_call_time_slots"


# ---------------------------------------------------------------------------
# reduce_consultation_state — READY_TO_SCHEDULE
# ---------------------------------------------------------------------------


def test_specific_time_with_digits_ready_to_schedule() -> None:
    s = _state(preferred_call_time="Friday at 3pm")
    decision = reduce_consultation_state(
        state=s,
        message="I'd like to book for Friday at 3pm.",
        intent=_consultation_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.READY_TO_SCHEDULE
    assert decision.can_schedule is True
    assert decision.timezone_needed is False


def test_relative_window_even_with_timezone_offers_slots() -> None:
    # Even with a known timezone, a vague window ("Friday afternoon") has no specific
    # clock time — booking it would silently coerce to 10 AM. Offer slots instead.
    s = _state(preferred_call_time="Friday afternoon", preferred_timezone="EST")
    decision = reduce_consultation_state(
        state=s,
        message="Let's go with Friday afternoon.",
        intent=_consultation_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED
    assert decision.can_schedule is False
    assert decision.next_question == "preferred_call_time_slots"


# ---------------------------------------------------------------------------
# reduce_consultation_state — SCHEDULED via action_result
# ---------------------------------------------------------------------------


def test_action_result_scheduled_returns_scheduled_stage() -> None:
    from bookcraft.components.actions.schemas import ActionType

    s = _state()
    action_result = MagicMock()
    action_result.success = True
    action_result.action_type = ActionType.SCHEDULE_CONSULTATION

    decision = reduce_consultation_state(
        state=s,
        message="Ok book it.",
        intent=_consultation_intent(),
        contact_ready=True,
        action_result=action_result,
    )
    assert decision.stage == ConsultationStage.SCHEDULED
    assert decision.can_schedule is False
    assert decision.stop_discovery is True


def test_confirmed_appointment_id_returns_scheduled_stage() -> None:
    s = _state(confirmed_appointment_id="appt-123")
    decision = reduce_consultation_state(
        state=s,
        message="Have my consultation been scheduled?",
        intent=_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.SCHEDULED
    assert decision.is_status_question is True


def test_handoff_created_returns_scheduled_stage() -> None:
    s = _state(handoff_created=True)
    decision = reduce_consultation_state(
        state=s,
        message="Is it booked?",
        intent=_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.SCHEDULED
    assert decision.is_status_question is True


# ---------------------------------------------------------------------------
# reduce_consultation_state — PENDING_CONFIRMATION
# ---------------------------------------------------------------------------


def test_pending_confirmation_from_plan() -> None:
    from bookcraft.components.actions.schemas import ActionStatus, ActionType

    s = _state()
    action_plan = MagicMock()
    action_plan.action_type = ActionType.SCHEDULE_CONSULTATION
    action_plan.status = ActionStatus.NEEDS_CONFIRMATION

    decision = reduce_consultation_state(
        state=s,
        message="Please book it.",
        intent=_consultation_intent(),
        contact_ready=True,
        action_plan=action_plan,
    )
    assert decision.stage == ConsultationStage.PENDING_CONFIRMATION
    assert decision.can_schedule is False
    assert decision.stop_discovery is True


def test_pending_confirmation_from_state() -> None:
    s = _state(pending_confirmation=True)
    decision = reduce_consultation_state(
        state=s,
        message="Yes please.",
        intent=_consultation_intent(),
        contact_ready=True,
    )
    assert decision.stage == ConsultationStage.PENDING_CONFIRMATION


# ---------------------------------------------------------------------------
# reduce_consultation_state — preferred_call_time extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected_fragment",
    [
        ("Call me Monday morning", "monday"),
        ("How about tomorrow afternoon?", "afternoon"),
        ("Friday at 2pm works", "2pm"),
        ("I'm free anytime", "anytime"),
    ],
)
def test_call_time_extracted_from_message(message: str, expected_fragment: str) -> None:
    s = _state()
    decision = reduce_consultation_state(
        state=s,
        message=message,
        intent=_consultation_intent(),
        contact_ready=True,
    )
    assert decision.preferred_call_time is not None
    assert expected_fragment in decision.preferred_call_time.casefold()
