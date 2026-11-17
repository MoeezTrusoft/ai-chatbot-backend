"""The call-now conversation (rules M-07 to M-09, H-03, H-04, K-03)."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from bookcraft.components.callbacks import (
    CallbackFlow,
    CallbackFlowState,
    CallbackHours,
    CallbackRequestService,
)
from bookcraft.components.callbacks.flow import MOBILE_MAX_CHARS, all_wordings
from bookcraft.components.callbacks.service import InMemoryCallbackRepository
from bookcraft.components.consultations.holidays import set_holiday_cache

WEDNESDAY_2PM = datetime(2026, 11, 18, 20, 0, tzinfo=UTC)
WEDNESDAY_655PM = datetime(2026, 11, 19, 0, 55, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_holidays():
    set_holiday_cache([])
    yield
    set_holiday_cache([])


@pytest.fixture
def flow() -> CallbackFlow:
    return CallbackFlow(hours=CallbackHours())


def _answer(flow: CallbackFlow, state: CallbackFlowState, message: str, **overrides):
    values = {
        "phone_known": True,
        "email_known": False,
        "phone_in_message": False,
        "visitor_tz": None,
        "now_utc": WEDNESDAY_2PM,
    }
    values.update(overrides)
    return flow.interpret(state, message=message, **values)


# ----------------------------------------------------------------------------- wording
def test_every_line_fits_the_mobile_limit_and_has_no_dash():
    for kind, lines in all_wordings().items():
        for line in lines:
            rendered = line.format(
                last4="1234",
                window="10 AM to 7 PM Central, Monday to Friday (11:30 PM to 8:30 AM your time)",
                hours_only="10 AM to 7 PM Central",
                minutes=5,
                time="Wednesday at 12:30pm",
                timezone="Argentina Buenos Aires time",
            )
            assert len(rendered) <= MOBILE_MAX_CHARS, (kind, len(rendered))
            assert not re.search("[‒-―]", rendered), kind
            assert rendered.count("?") <= 1, kind  # one question per message (M-03)


def test_offer_asks_for_the_number_when_none_is_known(flow: CallbackFlow):
    state = CallbackFlowState()
    text, _ = flow.offer_text(state, action="offer_now", phone_last4=None, reply_index=3)
    assert "right now" in text and "best number" in text
    assert state.pending == "offer_now"
    assert state.offers_this_visit == 1
    assert state.last_offer_reply_index == 3


def test_offer_quotes_the_last_four_digits_and_never_asks_for_the_number(flow: CallbackFlow):
    # Scenario 22 and K-03.
    text, _ = flow.offer_text(CallbackFlowState(), action="offer_now", phone_last4="3344")
    assert "ending in 3344" in text
    assert "best number" not in text


def test_schedule_offer_never_says_right_now(flow: CallbackFlow):
    # Scenarios 23 and 29.
    state = CallbackFlowState()
    text, _ = flow.offer_text(state, action="offer_schedule", phone_last4=None)
    assert "right now" not in text.lower()
    assert "10 AM to 7 PM Central" in text
    assert state.pending == "ask_time"


def test_schedule_offer_adds_the_visitors_local_hours(flow: CallbackFlow):
    text, _ = flow.offer_text(
        CallbackFlowState(),
        action="offer_schedule",
        phone_last4=None,
        visitor_tz="America/New_York",
        now_utc=WEDNESDAY_2PM,
    )
    assert "11 AM to 8 PM your time" in text


def test_the_same_wording_is_not_used_twice_in_a_visit(flow: CallbackFlow):
    # G-08.
    state = CallbackFlowState()
    first, _ = flow.offer_text(state, action="offer_now", phone_last4=None)
    second, _ = flow.offer_text(state, action="offer_now", phone_last4=None)
    assert first != second


# ----------------------------------------------------------------------------- answers
def test_yes_with_a_known_number_requests_the_call(flow: CallbackFlow):
    # Scenario 24.
    state = CallbackFlowState(pending="offer_now")
    result = _answer(flow, state, "yes")
    assert result.outcome == "request_now"
    assert result.request_mode == "now"
    assert "within 5 minutes" in (result.text or "")
    assert result.expects_reply is False and result.reply_kind == "call_confirmed"
    assert state.pending is None


def test_yes_without_a_number_asks_for_it_then_requests(flow: CallbackFlow):
    state = CallbackFlowState(pending="offer_now")
    first = _answer(flow, state, "sure", phone_known=False)
    assert first.outcome == "ask_phone_now"
    assert state.pending == "need_phone_now"
    second = _answer(flow, state, "555 201 3344", phone_known=True, phone_in_message=True)
    assert second.outcome == "request_now"


def test_a_number_alone_counts_as_yes(flow: CallbackFlow):
    state = CallbackFlowState(pending="offer_now")
    result = _answer(flow, state, "it's 555-201-3344", phone_in_message=True)
    assert result.outcome == "request_now"


def test_yes_after_the_cutoff_does_not_promise_five_minutes(flow: CallbackFlow):
    # Scenario 39: callback hours ended mid-conversation.
    state = CallbackFlowState(pending="offer_now")
    result = _answer(flow, state, "yes", now_utc=WEDNESDAY_655PM)
    assert result.outcome == "hours_closed"
    assert result.request_mode is None
    assert "5 minutes" not in (result.text or "")
    assert state.pending == "ask_time"


@pytest.mark.parametrize("message", ["not right now", "I'm at work", "later", "no"])
def test_not_now_asks_once_for_a_time(flow: CallbackFlow, message: str):
    state = CallbackFlowState(pending="offer_now")
    result = _answer(flow, state, message)
    assert result.outcome == "ask_time"
    assert (result.text or "").count("?") == 1
    assert state.pending == "ask_time"


def test_not_now_with_a_time_schedules_directly(flow: CallbackFlow):
    state = CallbackFlowState(pending="offer_now")
    result = _answer(flow, state, "not now, tomorrow at 3pm works")
    assert result.outcome == "request_scheduled"
    assert result.requested_time_text == "tomorrow at 3pm"


def test_a_time_inside_callback_hours_is_agreed_with_its_time_zone(flow: CallbackFlow):
    state = CallbackFlowState(pending="ask_time")
    result = _answer(flow, state, "Friday at 2pm", visitor_tz="America/New_York")
    assert result.outcome == "request_scheduled"
    assert result.request_mode == "scheduled"
    assert result.customer_timezone == "America/New_York"
    assert "Friday at 2pm" in (result.text or "") and "New York time" in (result.text or "")
    assert result.expects_reply is False and result.reply_kind == "call_scheduled"


def test_unknown_time_zone_is_stated_as_central(flow: CallbackFlow):
    result = _answer(flow, CallbackFlowState(pending="ask_time"), "tomorrow at 11am")
    assert "Central Time" in (result.text or "")
    assert result.customer_timezone == "America/Chicago"


def test_a_time_outside_callback_hours_is_not_accepted(flow: CallbackFlow):
    # Scenario 38.
    state = CallbackFlowState(pending="ask_time")
    result = _answer(flow, state, "can you call at 8:30pm")
    assert result.outcome == "ask_time_in_window"
    assert result.request_mode is None
    assert "10 AM to 7 PM Central" in (result.text or "")
    assert state.pending == "ask_time"


def test_visitor_local_time_is_checked_in_the_business_zone(flow: CallbackFlow):
    # 7:30 PM in New York is 6:30 PM Central: inside the window.
    inside = _answer(
        flow, CallbackFlowState(pending="ask_time"), "7:30pm", visitor_tz="America/New_York"
    )
    assert inside.outcome == "request_scheduled"
    # 9 AM in Los Angeles is 11 AM Central (inside); 8 AM in New York is 7 AM Central (outside).
    outside = _answer(
        flow, CallbackFlowState(pending="ask_time"), "8am", visitor_tz="America/New_York"
    )
    assert outside.outcome == "ask_time_in_window"


def test_a_weekend_day_is_not_accepted(flow: CallbackFlow):
    # Decision: callbacks run Monday to Friday only.
    result = _answer(flow, CallbackFlowState(pending="ask_time"), "Saturday at 11am")
    assert result.outcome == "ask_weekday"
    assert "Monday to Friday" in (result.text or "")


def test_a_time_without_a_number_asks_for_the_number_then_schedules(flow: CallbackFlow):
    state = CallbackFlowState(pending="ask_time")
    first = _answer(flow, state, "tomorrow at 1pm", phone_known=False)
    assert first.outcome == "ask_phone_scheduled"
    assert state.pending == "need_phone_scheduled"
    assert state.pending_time_text == "tomorrow at 1pm"
    second = _answer(flow, state, "555 201 3344", phone_known=True, phone_in_message=True)
    assert second.outcome == "request_scheduled"
    assert second.requested_time_text == "tomorrow at 1pm"


@pytest.mark.parametrize(
    "message",
    ["can we just chat here", "I'd rather keep texting", "please don't call me", "just email me"],
)
def test_preferring_chat_or_email_ends_the_call_flow(flow: CallbackFlow, message: str):
    state = CallbackFlowState(pending="offer_now")
    result = _answer(flow, state, message)
    assert result.outcome == "prefers_chat"
    assert state.declined_this_visit is True
    assert state.pending is None
    assert "email" in (result.text or "")  # no email on record: take one


def test_preferring_chat_does_not_ask_for_a_known_email(flow: CallbackFlow):
    result = _answer(
        flow, CallbackFlowState(pending="offer_now"), "can we just chat here", email_known=True
    )
    assert "email" not in (result.text or "").lower()


def test_an_unrelated_message_is_left_to_the_normal_reply(flow: CallbackFlow):
    state = CallbackFlowState(pending="offer_now")
    result = _answer(flow, state, "what genres do you edit?")
    assert result.handled is False
    assert state.pending is None  # the offer is not repeated on every turn


def test_time_question_is_asked_once_only(flow: CallbackFlow):
    state = CallbackFlowState(pending="ask_time")
    result = _answer(flow, state, "hmm let me think about it")
    assert result.handled is False
    assert state.pending is None


# ----------------------------------------------------------------------------- service
@pytest.mark.asyncio
async def test_a_repeated_request_is_stored_once():
    repository = InMemoryCallbackRepository()
    service = CallbackRequestService(repository=repository)
    thread_id = uuid4()
    kwargs = {
        "thread_id": thread_id,
        "customer_id": None,
        "visit_id": "visit-a",
        "mode": "now",
        "phone": "+1 555 201 3344",
        "name": "Dana",
        "requested_time_text": None,
        "customer_timezone": None,
        "summary": "Service: editing",
    }
    first = await service.request(**kwargs)
    second = await service.request(**kwargs)
    assert first.created is True and second.created is False
    assert first.callback_id == second.callback_id
    assert len(repository.rows) == 1
    assert first.promised_within_minutes == 5
    event = first.action_event()
    assert event["type"] == "callback_requested"
    assert event["phone_last4"] == "3344" and event["mode"] == "now"


@pytest.mark.asyncio
async def test_a_new_visit_or_a_new_time_is_a_new_request():
    repository = InMemoryCallbackRepository()
    service = CallbackRequestService(repository=repository)
    thread_id = uuid4()
    base = {
        "thread_id": thread_id, "customer_id": None, "phone": "5552013344", "name": None,
        "customer_timezone": "America/Chicago", "summary": "",
    }
    requests = (("a", "Friday at 2pm"), ("a", "Monday at 11am"), ("b", "Friday at 2pm"))
    for visit_id, time_text in requests:
        await service.request(
            visit_id=visit_id, mode="scheduled", requested_time_text=time_text, **base
        )
    assert len(repository.rows) == 3


@pytest.mark.asyncio
async def test_a_request_needs_a_phone_number():
    with pytest.raises(ValueError, match="callback_requires_phone"):
        await CallbackRequestService().request(
            thread_id=uuid4(), customer_id=None, visit_id=None, mode="now", phone="",
            name=None, requested_time_text=None, customer_timezone=None, summary="",
        )
