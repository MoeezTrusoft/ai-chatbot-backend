"""Indefinite consultation time → offer concrete half-hour slots.

Covers the "narrow an indefinite time to a definite one" behaviour:
 - the slot generator only produces in-window, half-hour, weekday openings
 - the canonical reducer routes a vague time to REQUESTED_TIME_SLOTS_OFFERED
 - the generator renders concrete options from the context pack
 - the planner asks the slot question instead of confirming a vague booking
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from bookcraft.components.consultations.slots import (
    suggest_consultation_slot_labels,
    suggest_consultation_slots,
)
from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.response.generator import _question_for_missing_fact
from bookcraft.components.response.planner import _next_question
from bookcraft.components.sales.consultation_state import (
    ConsultationStage,
    is_definite_call_time,
    reduce_consultation_state,
)

CT = ZoneInfo("America/Chicago")


# ---------------------------------------------------------------------------
# Slot generator
# ---------------------------------------------------------------------------


def test_slots_are_in_business_window_on_the_half_hour() -> None:
    now = datetime(2026, 6, 19, 9, 0, tzinfo=CT)  # Friday morning
    slots = suggest_consultation_slots(now=now, count=4)
    assert len(slots) == 4
    for slot in slots:
        assert slot.start.weekday() < 5  # Mon–Fri only
        assert 10 <= slot.start.hour < 19  # within 10 AM–7 PM
        assert slot.start.minute in (0, 30)  # half-hour grid
        assert slot.start > now  # never "now" or the past
        assert "CT" in slot.label


def test_slots_after_hours_roll_to_next_business_day() -> None:
    now = datetime(2026, 6, 19, 18, 45, tzinfo=CT)  # Friday 6:45 PM (past last slot)
    labels = suggest_consultation_slot_labels(now=now, count=2)
    # 6:45 PM Friday → next opening is Monday (skips the weekend).
    assert any("Monday" in label for label in labels)
    assert all("Saturday" not in label and "Sunday" not in label for label in labels)


def test_slots_are_distinct() -> None:
    now = datetime(2026, 6, 19, 10, 0, tzinfo=CT)
    starts = [s.start for s in suggest_consultation_slots(now=now, count=5)]
    assert len(set(starts)) == len(starts)


# ---------------------------------------------------------------------------
# Definiteness detector
# ---------------------------------------------------------------------------


def test_definite_requires_day_and_clock() -> None:
    assert is_definite_call_time("Tuesday at 3pm") is True
    assert is_definite_call_time("June 24 at 10:30am") is True
    assert is_definite_call_time("tomorrow at 2pm") is True
    for vague in ("anytime", "next week", "Friday", "afternoon", "3pm", "whenever", None, ""):
        assert is_definite_call_time(vague) is False


# ---------------------------------------------------------------------------
# Canonical reducer
# ---------------------------------------------------------------------------


def _consultation_state(preferred_call_time: str | None) -> SimpleNamespace:
    consultation = SimpleNamespace(
        requested=True,
        preferred_time_window=None,
        confirmed_appointment_id=None,
        pending_confirmation=False,
        customer_timezone=None,
    )
    return SimpleNamespace(
        sales_actions=SimpleNamespace(consultation=consultation),
        preferred_call_time=preferred_call_time,
        preferred_timezone=None,
        consultation_handoff_created=False,
        consultation_stage=ConsultationStage.REQUESTED_TIME_NEEDED,
        personal=None,
    )


def _intent():
    from bookcraft.domain.enums import QueryIntentType

    return SimpleNamespace(query_primary=QueryIntentType.CONSULTATION_REQUEST)


def test_reducer_offers_slots_for_indefinite_time() -> None:
    decision = reduce_consultation_state(
        state=_consultation_state("anytime next week"),
        message="anytime next week works",
        intent=_intent(),
        contact_ready=True,
        prior_stage=ConsultationStage.REQUESTED_TIME_NEEDED,
    )
    assert decision.stage == ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED
    assert decision.next_question == "preferred_call_time_slots"
    assert decision.can_schedule is False


def test_reducer_hard_gates_scheduling_without_phone() -> None:
    # Consultation requires a phone: even a fully definite time cannot be scheduled
    # while the contact is email-only.
    decision = reduce_consultation_state(
        state=_consultation_state("Tuesday at 3pm"),
        message="Tuesday at 3pm please",
        intent=_intent(),
        contact_ready=True,
        has_email=True,
        has_phone=False,
        require_phone=True,
        prior_stage=ConsultationStage.REQUESTED_TIME_NEEDED,
    )
    assert decision.stage == ConsultationStage.REQUESTED_PHONE_NEEDED
    assert decision.next_question == "missing_phone"
    assert decision.can_schedule is False


def test_reducer_schedules_with_phone_present() -> None:
    decision = reduce_consultation_state(
        state=_consultation_state("Tuesday at 3pm"),
        message="Tuesday at 3pm please",
        intent=_intent(),
        contact_ready=True,
        has_email=True,
        has_phone=True,
        require_phone=True,
        prior_stage=ConsultationStage.REQUESTED_TIME_NEEDED,
    )
    assert decision.stage == ConsultationStage.READY_TO_SCHEDULE
    assert decision.can_schedule is True


def test_reducer_schedules_for_definite_time() -> None:
    decision = reduce_consultation_state(
        state=_consultation_state("Tuesday at 3pm"),
        message="Tuesday at 3pm please",
        intent=_intent(),
        contact_ready=True,
        prior_stage=ConsultationStage.REQUESTED_TIME_NEEDED,
    )
    assert decision.stage == ConsultationStage.READY_TO_SCHEDULE
    assert decision.can_schedule is True


# ---------------------------------------------------------------------------
# Generator rendering
# ---------------------------------------------------------------------------


def test_generator_renders_concrete_slots() -> None:
    pack = ContextPack(
        suggested_call_slots=[
            "Friday, Jun 19 at 10:00 AM CT",
            "Friday, Jun 19 at 1:00 PM CT",
            "Monday, Jun 22 at 10:00 AM CT",
        ]
    )
    text = _question_for_missing_fact("preferred_call_time_slots", context_pack=pack)
    assert text is not None
    assert "10:00 AM" in text
    assert "Monday, Jun 22" in text
    # All three offered options appear.
    assert text.count(" CT") >= 3


def test_generator_falls_back_when_no_slots() -> None:
    pack = ContextPack(suggested_call_slots=[])
    text = _question_for_missing_fact("preferred_call_time_slots", context_pack=pack)
    assert text is not None
    assert "specific day and time" in text.lower()


# ---------------------------------------------------------------------------
# Planner next-question
# ---------------------------------------------------------------------------


def test_planner_asks_slot_question_for_indefinite_time() -> None:
    pack = ContextPack(preferred_call_time="Friday afternoon")
    decision = SimpleNamespace(next_question="preferred_call_time_slots")
    nq = _next_question(
        intent=_intent(),
        context_pack=pack,
        primary_goal="consultation_time_capture",
        consultation_objective_decision=decision,
    )
    assert nq == "preferred_call_time_slots"


def test_planner_confirms_handoff_only_for_definite_time() -> None:
    # Vague time on file → must not silently confirm; ask for slots.
    pack_vague = ContextPack(preferred_call_time="anytime")
    assert (
        _next_question(
            intent=_intent(),
            context_pack=pack_vague,
            primary_goal="consultation_handoff_confirmation",
        )
        == "preferred_call_time_slots"
    )
    # Definite time on file → ready to confirm (no further question).
    pack_definite = ContextPack(preferred_call_time="Tuesday at 3pm")
    assert (
        _next_question(
            intent=_intent(),
            context_pack=pack_definite,
            primary_goal="consultation_handoff_confirmation",
        )
        is None
    )
