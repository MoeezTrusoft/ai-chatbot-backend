"""Call opt-out + deferral detection and their effect on the reducer (chat 6816).

The customer asked to be texted four times and postponed twice; the bot answered
every one of those with another "what time works for your call?".
"""

from bookcraft.components.sales.consultation_preferences import (
    ConsultationPreferenceDetector,
)
from bookcraft.components.sales.consultation_state import (
    ConsultationStage,
    reduce_consultation_state,
)
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState


class _Intent:
    query_primary = QueryIntentType.CONSULTATION_REQUEST
    service_primary = None


def _ready_state() -> ThreadState:
    state = ThreadState()
    state.lead_created = True
    state.contact_info = {
        "name": "Priya",
        "email": "author@example.com",
        "phone": "512-555-0142",
    }
    return state


def _reduce(state: ThreadState, message: str, **kw):
    return reduce_consultation_state(
        state=state,
        message=message,
        intent=_Intent(),
        contact_ready=True,
        has_email=True,
        has_phone=True,
        require_phone=True,
        prior_stage=state.consultation_stage,
        **kw,
    )


class TestCallOptOutDetection:
    def test_real_transcript_optouts_are_detected(self) -> None:
        detector = ConsultationPreferenceDetector()
        for msg in (
            "can they text i'm really bad at calling",
            "any time works if you text me",
            "can we text instead",
            "can he text me please",
            "texting is better for me",
            "i dont like phone calls",
        ):
            assert detector.detect(msg).call_opt_out is True, msg

    def test_explicit_call_request_is_opt_in(self) -> None:
        result = ConsultationPreferenceDetector().detect("you can just call me")
        assert result.call_opt_in is True
        assert result.call_opt_out is False

    def test_contradictory_message_records_neither(self) -> None:
        # Offers a call AND asks for a text in one breath — ambiguous, don't guess.
        result = ConsultationPreferenceDetector().detect("you can call me or text me instead")
        assert result.call_opt_out is False
        assert result.call_opt_in is False

    def test_instead_of_calling_is_not_swallowed_as_ambiguous(self) -> None:
        # Guard against over-triggering the ambiguity rule: naming the call only
        # to reject it is a clean opt-out, not a contradiction.
        result = ConsultationPreferenceDetector().detect("can they text me instead of calling")
        assert result.call_opt_out is True


class TestDeferralDetection:
    def test_real_transcript_deferrals_are_detected(self) -> None:
        detector = ConsultationPreferenceDetector()
        for msg in (
            "okay so we might need to do it next month",
            "but I'm not doing it until next month",
            "not right now",
            "can we hold off until next month",
        ):
            assert detector.detect(msg).deferred is True, msg

    def test_future_plans_are_not_deferrals(self) -> None:
        # A bare horizon with no postponement cue must NOT park the booking.
        detector = ConsultationPreferenceDetector()
        for msg in (
            "my book comes out next month",
            "the launch is next month",
            "Friday works best at 2",
            "9-12 works best",
        ):
            assert detector.detect(msg).deferred is False, msg

    def test_ready_to_book_cancels_a_deferral(self) -> None:
        result = ConsultationPreferenceDetector().detect("lets go ahead and book it")
        assert result.defer_cancelled is True

    def test_defer_hint_is_captured(self) -> None:
        result = ConsultationPreferenceDetector().detect(
            "okay so we might need to do it next month"
        )
        assert result.defer_hint is not None
        assert "next month" in result.defer_hint


class TestReducerHonoursPreferences:
    def test_call_opt_out_stops_the_call_time_ladder(self) -> None:
        state = _ready_state()
        decision = _reduce(state, "can they text i'm really bad at calling", call_opt_out=True)
        assert decision.stage is ConsultationStage.TEXT_FOLLOWUP_PREFERRED
        # The whole point: no further call-scheduling interrogation.
        assert decision.next_question is None
        assert decision.can_schedule is False

    def test_deferral_stops_every_booking_ask(self) -> None:
        state = _ready_state()
        decision = _reduce(state, "but I'm not doing it until next month", consultation_deferred=True)
        assert decision.stage is ConsultationStage.DEFERRED
        assert decision.next_question is None
        assert decision.can_schedule is False

    def test_deferral_outranks_the_phone_gate(self) -> None:
        # Demanding a number to ring them at, after they said "not yet", is the
        # steamroll we're removing. No phone on state here.
        state = ThreadState()
        state.lead_created = True
        decision = reduce_consultation_state(
            state=state,
            message="not right now",
            intent=_Intent(),
            contact_ready=True,
            has_email=True,
            has_phone=False,
            require_phone=True,
            consultation_deferred=True,
            prior_stage=None,
        )
        assert decision.stage is ConsultationStage.DEFERRED
        assert decision.next_question is None

    def test_call_opt_out_still_asks_for_a_phone_to_text(self) -> None:
        # A text follow-up needs a number, so the phone gate still runs first.
        state = ThreadState()
        state.lead_created = True
        decision = reduce_consultation_state(
            state=state,
            message="can they text me instead",
            intent=_Intent(),
            contact_ready=True,
            has_email=True,
            has_phone=False,
            require_phone=True,
            call_opt_out=True,
            prior_stage=None,
        )
        assert decision.stage is ConsultationStage.REQUESTED_PHONE_NEEDED
        assert decision.next_question == "missing_phone"


class TestBookingLoopTerminates:
    """The control path: a customer who DOES want a call must reach a booking."""

    def test_day_then_range_then_pick_reaches_ready_to_schedule(self) -> None:
        state = _ready_state()
        state.personal.timezone.value = "America/Chicago"

        first = _reduce(state, "anytime on Wednesday works")
        state.preferred_call_time = first.preferred_call_time
        state.consultation_stage = str(first.stage)
        assert first.stage is ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED

        second = _reduce(state, "9-12 works best")
        state.preferred_call_time = second.preferred_call_time
        state.consultation_stage = str(second.stage)
        assert second.stage is ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED
        assert second.preferred_call_time == "Wednesday between 9am and 12pm"

        third = _reduce(state, "probably 12 would work the best")
        assert third.stage is ConsultationStage.READY_TO_SCHEDULE
        assert third.preferred_call_time == "Wednesday at 12pm"
        assert third.can_schedule is True

    def test_known_time_survives_a_turn_that_does_not_restate_it(self) -> None:
        state = _ready_state()
        state.personal.timezone.value = "America/Chicago"
        state.preferred_call_time = "Wednesday at 12pm"
        state.consultation_stage = str(ConsultationStage.READY_TO_SCHEDULE)

        decision = _reduce(state, "ok thanks")
        assert decision.preferred_call_time == "Wednesday at 12pm"
        assert decision.stage is ConsultationStage.READY_TO_SCHEDULE
