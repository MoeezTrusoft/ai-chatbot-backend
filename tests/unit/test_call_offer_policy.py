"""When the mobile call offer is made (rules M-06 and M-10)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bookcraft.components.callbacks import CallbackFlowState, CallbackHours, CallOfferPolicy
from bookcraft.components.consultations.holidays import set_holiday_cache

WEDNESDAY_2PM = datetime(2026, 11, 18, 20, 0, tzinfo=UTC)
WEDNESDAY_655PM = datetime(2026, 11, 19, 0, 55, tzinfo=UTC)
WEDNESDAY_11PM = datetime(2026, 11, 19, 5, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_holidays():
    set_holiday_cache([])
    yield
    set_holiday_cache([])


def _decide(policy: CallOfferPolicy | None = None, **overrides):
    values = {
        "device": "mobile",
        "flow": CallbackFlowState(),
        "message": "tell me more",
        "reply_index": 1,
        "service_known": False,
        "stage_known": False,
        "now_utc": WEDNESDAY_2PM,
    }
    values.update(overrides)
    return (policy or CallOfferPolicy(hours=CallbackHours())).decide(**values)


def test_no_offer_on_desktop_by_default():
    decision = _decide(device="desktop", service_known=True, stage_known=True)
    assert decision.action == "none"
    assert decision.hold_reason == "device_not_eligible"


def test_desktop_can_be_switched_on():
    policy = CallOfferPolicy(hours=CallbackHours(), desktop_enabled=True)
    assert _decide(policy, device="desktop", service_known=True, stage_known=True).offer


def test_no_offer_before_anything_is_known_and_before_the_sixth_reply():
    decision = _decide(reply_index=5)
    assert decision.action == "none"
    assert decision.hold_reason == "not_due"


def test_offer_by_the_sixth_reply_at_the_latest():
    # Scenario 20.
    decision = _decide(reply_index=6)
    assert decision.action == "offer_now"
    assert decision.trigger == "reply_limit_reached"


def test_offer_as_soon_as_service_and_stage_are_known():
    # Scenario 30: everything was learned from the first message.
    decision = _decide(reply_index=1, service_known=True, stage_known=True)
    assert decision.action == "offer_now"
    assert decision.trigger == "service_and_stage_known"


@pytest.mark.parametrize(
    ("message", "trigger"),
    [
        ("How do I get started?", "asked_how_to_start"),  # scenario 21
        ("what's the next step", "asked_how_to_start"),
        ("can I speak to someone", "asked_for_person"),
        ("could you call me", "asked_for_person"),
    ],
)
def test_offer_at_once_on_a_buying_signal(message: str, trigger: str):
    decision = _decide(message=message, reply_index=2)
    assert decision.action == "offer_now"
    assert decision.trigger == trigger


def test_price_question_is_a_buying_signal():
    assert _decide(asked_price=True).trigger == "asked_price"


def test_ordinary_talk_about_a_person_is_not_a_request_for_one():
    assert not _decide(message="I want to talk to my editor about chapter two").offer


def test_schedule_offer_after_the_cutoff_and_at_night():
    # Scenarios 23 and 29: never "right now" outside callback hours.
    assert _decide(reply_index=6, now_utc=WEDNESDAY_655PM).action == "offer_schedule"
    assert _decide(reply_index=6, now_utc=WEDNESDAY_11PM).action == "offer_schedule"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"complaint": True}, "complaint_or_objection"),
        ({"needs_clarification": True}, "clarification_first"),
        ({"call_opt_out": True}, "prefers_chat_or_email"),
        ({"deferred": True}, "postponed"),
        ({"consultation_booked": True}, "consultation_already_booked"),
        ({"confirmation_pending": True}, "other_confirmation_pending"),
        ({"csr_active": True}, "csr_active"),
    ],
)
def test_hold_back_wins_over_the_sixth_reply(override: dict, reason: str):
    # M-10 takes precedence over M-06.
    decision = _decide(reply_index=6, **override)
    assert decision.action == "none"
    assert decision.hold_reason == reason
    assert decision.trigger == "reply_limit_reached"


def test_only_researching_holds_until_a_new_buying_signal():
    flow = CallbackFlowState(researching=True)
    assert _decide(flow=flow, reply_index=6).hold_reason == "only_researching"
    assert _decide(flow=flow, reply_index=7, message="how do I get started?").offer


def test_researching_cue_is_recognised():
    assert CallOfferPolicy.said_researching("I'm just researching for now")
    assert CallOfferPolicy.said_researching("not ready to commit")
    assert not CallOfferPolicy.said_researching("I am researching Victorian London for my novel")


def test_second_offer_needs_a_new_buying_signal_and_there_is_no_third():
    once = CallbackFlowState(offers_this_visit=1)
    assert _decide(flow=once, reply_index=8).hold_reason == "second_offer_needs_buying_signal"
    assert _decide(flow=once, reply_index=8, asked_price=True).offer
    twice = CallbackFlowState(offers_this_visit=2)
    assert _decide(flow=twice, reply_index=9, asked_price=True).hold_reason == "offer_cap_reached"


def test_nothing_more_to_offer_once_a_callback_is_requested_or_declined():
    assert (
        _decide(flow=CallbackFlowState(requested_this_visit=True), reply_index=6).hold_reason
        == "callback_already_requested"
    )
    assert (
        _decide(flow=CallbackFlowState(declined_this_visit=True), asked_price=True).hold_reason
        == "prefers_chat_or_email"
    )
