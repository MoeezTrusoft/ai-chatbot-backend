"""Visitor Messaging Rule Set v2.4 through the turn API.

Covers the engine side of the rule set: visitor attributes, mobile reply limits,
the call-now offer and its answers, proactive-message context, the
``expects_reply`` flag and the learn-only endpoint. Scenario numbers refer to
section 13 of the rule set.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.consultations.holidays import set_holiday_cache
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings

WEDNESDAY_2PM = datetime(2026, 11, 18, 20, 0, tzinfo=UTC)   # 2:00 PM Central
WEDNESDAY_655PM = datetime(2026, 11, 19, 0, 55, tzinfo=UTC)  # 6:55 PM Central
WEDNESDAY_11PM = datetime(2026, 11, 19, 5, 0, tzinfo=UTC)    # 11:00 PM Central
SATURDAY_2PM = datetime(2026, 11, 21, 20, 0, tzinfo=UTC)

LONG_ANSWER = (
    "Editing a finished thriller usually starts with a manuscript review so the editor can "
    "see which level of editing it needs. From there we match you with an editor who works in "
    "your genre and agree the scope. The editor then returns a marked up manuscript with "
    "notes on pacing, plot and character. Would you like to know how the stages differ? "
    "I can also explain what proofreading adds at the end."
)


class _FakeGenerator:
    """Stands in for the LLM so the reply text is known."""

    adapter = None

    def __init__(self, text: str = LONG_ANSWER) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> ResponseDraft:
        self.calls.append(kwargs)
        return ResponseDraft(text=self.text, source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


@pytest.fixture(autouse=True)
def _no_holidays():
    set_holiday_cache([])
    yield
    set_holiday_cache([])


@pytest.fixture
def chat():
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        service = client.app.state.chat_service
        generator = _FakeGenerator()
        service.response_generator = generator
        service.clock = lambda: WEDNESDAY_2PM
        yield _Chat(client, service, generator)


class _Chat:
    def __init__(self, client: TestClient, service: Any, generator: _FakeGenerator) -> None:
        self.client = client
        self.service = service
        self.generator = generator
        self.thread_id = str(uuid4())
        self.visit_id = "visit-1"

    def at(self, moment: datetime) -> None:
        self.service.clock = lambda: moment

    def turn(self, message: str, **extra: object) -> dict[str, Any]:
        payload: dict[str, object] = {
            "thread_id": self.thread_id,
            "message": message,
            "device": "mobile",
            "visit_id": self.visit_id,
            **extra,
        }
        response = self.client.post("/api/v1/chat/turn", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    def state(self) -> Any:
        from uuid import UUID

        return self.service.threads[UUID(self.thread_id)].state

    def callbacks(self) -> list[dict[str, Any]]:
        return list(self.service.callback_service.repository.rows.values())


def _texts(response: dict[str, Any]) -> list[str]:
    return [bubble["text"] for bubble in response["bubbles"]]


def _events(response: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [event for event in response["action_events"] if event.get("type") == kind]


# --------------------------------------------------------------------------- attributes
def test_requests_without_the_new_fields_behave_as_before(chat: _Chat):
    response = chat.client.post(
        "/api/v1/chat/turn", json={"thread_id": chat.thread_id, "message": "Tell me about editing"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["expects_reply"] is True and body["reply_kind"] is None
    assert not _events(body, "callback_requested")
    assert chat.state().device is None
    # Desktop keeps the paragraph split at 500 characters.
    assert all(len(text) <= 500 for text in _texts(body))


def test_unknown_device_value_is_rejected(chat: _Chat):
    response = chat.client.post(
        "/api/v1/chat/turn", json={"message": "hello", "device": "watch"}
    )
    assert response.status_code == 422


def test_visitor_attributes_are_stored_on_the_thread(chat: _Chat):
    chat.turn(
        "hello there",
        visitor_type="returning",
        signup_status="completed",
        visitor_timezone="America/New_York",
    )
    state = chat.state()
    assert (state.device, state.visitor_type, state.signup_status) == (
        "mobile", "returning", "completed",
    )
    assert state.visitor_timezone == "America/New_York"


# ----------------------------------------------------------------------------- limits
def test_mobile_reply_is_at_most_two_bubbles_of_200_characters(chat: _Chat):
    # Scenario 19 and rule M-02.
    body = chat.turn("hello there")
    texts = _texts(body)
    assert 1 <= len(texts) <= 2
    assert all(len(text) <= 200 for text in texts)
    # The reply ends at a sentence boundary, not mid-thought.
    assert texts[-1].rstrip()[-1] in ".!?"


def test_desktop_reply_is_not_shortened(chat: _Chat):
    body = chat.turn("hello there", device="desktop")
    joined = " ".join(_texts(body))
    # Nothing is cut: the last sentence of the full answer is still there.
    assert "proofreading adds" in joined
    assert len(joined) > 300


def test_model_is_told_the_visitor_is_on_a_phone(chat: _Chat):
    chat.turn("hello there")
    hint = chat.generator.calls[-1]["response_hint"] or ""
    assert "MOBILE VISITOR" in hint
    chat.thread_id = str(uuid4())
    chat.turn("hello there", device="desktop")
    assert "MOBILE VISITOR" not in (chat.generator.calls[-1]["response_hint"] or "")


# ------------------------------------------------------------------------- call offer
def test_call_offer_when_the_visitor_asks_how_to_start(chat: _Chat):
    # Scenario 21: the offer is made in that reply, without waiting for the sixth.
    chat.turn("I have a memoir I am working on")
    body = chat.turn("How do I get started?")
    texts = _texts(body)
    assert len(texts) <= 2
    assert "right now" in texts[-1] and "best number" in texts[-1]
    # One question per message: only the offer asks.
    assert sum(text.count("?") for text in texts) == 1
    assert all(len(text) <= 200 for text in texts)
    assert not _events(body, "show_consultation_form")
    assert chat.state().callback.pending == "offer_now"
    hint = chat.generator.calls[-1]["response_hint"] or ""
    assert "CALL OFFER" in hint


def test_call_offer_by_the_sixth_reply(chat: _Chat):
    # Scenario 20. Six neutral messages; nothing else triggers the offer.
    chat.generator.text = "Thanks for sharing that."
    bodies = [chat.turn("ok thanks") for _ in range(6)]
    offers = [i for i, body in enumerate(bodies, 1) if "right now" in " ".join(_texts(body))]
    assert offers, "an offer must be made by the sixth reply"
    assert offers[0] <= 6


def test_no_call_offer_on_desktop(chat: _Chat):
    chat.turn("I have a memoir I am working on", device="desktop")
    body = chat.turn("How do I get started?", device="desktop")
    assert "right now" not in " ".join(_texts(body))
    assert chat.state().callback.offers_this_visit == 0


def test_offer_after_the_cutoff_asks_for_a_time_and_never_says_right_now(chat: _Chat):
    # Scenario 23.
    chat.at(WEDNESDAY_655PM)
    body = chat.turn("How do I get started?")
    joined = " ".join(_texts(body))
    assert "right now" not in joined.lower()
    assert "10 AM to 7 PM Central" in joined
    assert chat.state().callback.pending == "ask_time"


@pytest.mark.parametrize("moment", [WEDNESDAY_11PM, SATURDAY_2PM])
def test_offer_outside_callback_days_and_hours_asks_for_a_time(chat: _Chat, moment: datetime):
    # Scenario 29, and the Monday to Friday decision.
    chat.at(moment)
    body = chat.turn("can I speak to someone?")
    joined = " ".join(_texts(body))
    assert "right now" not in joined.lower()
    assert "Monday to Friday" in joined


def test_yes_to_the_offer_requests_a_callback_and_expects_no_reply(chat: _Chat):
    # Scenario 24.
    chat.turn("How do I get started?")
    need_number = chat.turn("yes please")
    assert "best number" in " ".join(_texts(need_number))
    assert not chat.callbacks()
    calls_before = len(chat.generator.calls)

    body = chat.turn("555 201 3344")
    assert _texts(body) == ["Thank you. You'll get a call from our team within 5 minutes."]
    assert body["expects_reply"] is False and body["reply_kind"] == "call_confirmed"
    # A fixed template: the model was not called for this reply.
    assert len(chat.generator.calls) == calls_before
    [event] = _events(body, "callback_requested")
    assert event["mode"] == "now" and event["phone_last4"] == "3344"
    assert event["promised_within_minutes"] == 5
    [row] = chat.callbacks()
    assert row["mode"] == "now" and row["status"] == "requested"
    assert chat.state().callback.requested_this_visit is True
    # No further offer in this visit.
    later = chat.turn("How do I get started with editing too?")
    assert "right now" not in " ".join(_texts(later))


def test_known_number_is_confirmed_by_its_last_four_digits(chat: _Chat):
    # Scenario 22 and K-03: the number came from the signup form.
    facts = chat.client.post(
        "/api/v1/chat/facts",
        json={
            "thread_id": chat.thread_id, "name": "Dana Reyes", "phone": "+1 555 201 3344",
            "source_label": "crm_sync",
        },
    )
    assert facts.status_code == 200 and "phone" in facts.json()["fields_applied"]
    body = chat.turn("How do I get started?", signup_status="completed")
    offer = _texts(body)[-1]
    assert "ending in 3344" in offer
    assert "best number" not in offer
    confirmed = chat.turn("yes")
    assert "within 5 minutes" in " ".join(_texts(confirmed))
    [row] = chat.callbacks()
    assert row["customer_phone"].endswith("3344")


def test_a_repeated_yes_does_not_create_a_second_callback(chat: _Chat):
    chat.turn("my number is 555 201 3344")
    chat.turn("How do I get started?")
    chat.turn("yes")
    chat.state().callback.pending = "offer_now"  # a duplicated or replayed answer
    chat.state().callback.requested_this_visit = False
    chat.turn("yes")
    assert len(chat.callbacks()) == 1


def test_not_now_then_a_time_schedules_a_callback(chat: _Chat):
    chat.turn("my number is 555 201 3344")
    chat.turn("can I speak to someone?")
    ask = chat.turn("not right now", visitor_timezone="America/New_York")
    assert "What time suits you?" in " ".join(_texts(ask))
    body = chat.turn("tomorrow at 3pm", visitor_timezone="America/New_York")
    assert "tomorrow at 3pm" in " ".join(_texts(body))
    assert body["expects_reply"] is False and body["reply_kind"] == "call_scheduled"
    [event] = _events(body, "callback_requested")
    assert event["mode"] == "scheduled"
    assert event["requested_time_text"] == "tomorrow at 3pm"
    assert event["customer_timezone"] == "America/New_York"


def test_a_call_time_outside_callback_hours_is_declined(chat: _Chat):
    # Scenario 38.
    chat.turn("my number is 555 201 3344")
    chat.turn("can I speak to someone?")
    chat.turn("later")
    body = chat.turn("8:30 PM")
    joined = " ".join(_texts(body))
    assert "10 AM to 7 PM Central" in joined
    assert not chat.callbacks()


def test_yes_after_callback_hours_ended_is_not_promised_five_minutes(chat: _Chat):
    # Scenario 39.
    chat.turn("my number is 555 201 3344")
    chat.turn("How do I get started?")
    chat.at(WEDNESDAY_655PM)
    body = chat.turn("yes")
    joined = " ".join(_texts(body))
    assert "5 minutes" not in joined
    assert "What time suits you?" in joined
    assert not chat.callbacks()


def test_preferring_chat_stops_further_call_offers(chat: _Chat):
    chat.turn("How do I get started?")
    body = chat.turn("can we just chat here instead")
    assert "keep going right here" in " ".join(_texts(body))
    assert chat.state().call_opt_out is True
    later = chat.turn("How do I get started with cover design?")
    assert "right now" not in " ".join(_texts(later))


def test_at_most_two_offers_per_visit_and_a_new_visit_starts_again(chat: _Chat):
    chat.generator.text = "Happy to explain."
    first = chat.turn("How do I get started?")
    assert "right now" in " ".join(_texts(first))
    chat.turn("what genres do you edit?")          # ignores the offer
    second = chat.turn("ok, how do I get started?")  # new buying signal: second offer
    assert "right now" in " ".join(_texts(second))
    assert _texts(first)[-1] != _texts(second)[-1]   # no repeated wording (G-08)
    chat.turn("what genres do you edit?")
    third = chat.turn("so how do I get started?")
    assert "right now" not in " ".join(_texts(third))
    assert chat.state().callback.offers_this_visit == 2

    chat.visit_id = "visit-2"
    again = chat.turn("How do I get started?")
    assert "right now" in " ".join(_texts(again))
    assert chat.state().callback.offers_this_visit == 1
    assert chat.state().visit_bot_replies == 1


def test_no_offer_while_a_complaint_is_being_raised(chat: _Chat):
    body = chat.turn(
        "this is frustrating, you are not answering my question. how do I get started?"
    )
    assert "right now" not in " ".join(_texts(body))
    assert chat.state().callback.offers_this_visit == 0


# ------------------------------------------------------------------- proactive context
def test_proactive_messages_join_the_conversation_context(chat: _Chat):
    # K-06: a visitor who answers a prompt is answering that prompt.
    prompt = "I see you're looking at editing. Would you like a quick overview of how it works?"
    chat.turn(
        "yes",
        proactive_messages=[
            {"rule_id": "T-SERVICE", "text": prompt, "sent_at": "2026-11-18T20:00:00Z"}
        ],
    )
    turns = chat.generator.calls[-1]["recent_turns"]
    assert turns and turns[-1][1] == prompt
    # A proactive message is not a bot reply (M-06 counts replies only).
    assert chat.state().visit_bot_replies == 1


def test_too_many_proactive_messages_are_rejected(chat: _Chat):
    response = chat.client.post(
        "/api/v1/chat/turn",
        json={"message": "hi", "proactive_messages": [{"rule_id": "I-01", "text": "x"}] * 11},
    )
    assert response.status_code == 422


def test_a_full_visit_of_proactive_messages_is_accepted(chat: _Chat):
    # Opening, two idle follow-ups, three browsing prompts and the thank-you.
    messages = [{"rule_id": f"R-{index}", "text": f"Prompt number {index}?"} for index in range(7)]
    chat.turn("hello", proactive_messages=messages)
    turns = chat.generator.calls[-1]["recent_turns"]
    assert len(turns) == 5 and turns[-1][1] == "Prompt number 6?"


# ----------------------------------------------------------------------- learn-only
def test_observe_learns_without_replying(chat: _Chat):
    response = chat.client.post(
        "/api/v1/chat/observe",
        json={
            "thread_id": chat.thread_id,
            "message": "My email is dana@example.com and my thriller is 70,000 words",
        },
    )
    assert response.status_code == 204
    state = chat.state()
    assert state.personal.email.value == "dana@example.com"
    assert state.project.word_count.value == 70000
    assert state.visit_bot_replies == 0
    assert not chat.generator.calls


def test_contact_details_are_learned_from_a_message_in_another_language(chat: _Chat):
    # K-06: the English-only redirect must not cost us a detail the visitor gave.
    body = chat.turn("mera email dana@example.com hai, mujhe madad chahiye kitab ke liye")
    assert body["language_status"] != "en"
    assert chat.state().contact_info.get("email") == "dana@example.com"


def test_no_dashes_in_any_call_flow_reply(chat: _Chat):
    chat.generator.text = "Happy to explain."
    replies: list[str] = []
    replies += _texts(chat.turn("How do I get started?"))
    replies += _texts(chat.turn("not now"))
    replies += _texts(chat.turn("Saturday at 11am"))
    replies += _texts(chat.turn("Friday at 11am"))
    for text in replies:
        assert not re.search("[‒-―]", text), text


# ------------------------------------------------------------------ one-word answers
def test_yes_to_is_your_manuscript_finished_is_stored(chat: _Chat):
    # Scenario 35: a one-word reply is understood in the light of the question.
    chat.generator.text = "Happy to help with editing. Is your manuscript finished?"
    chat.turn("I need help with editing", device="desktop")
    assert chat.state().project.manuscript_status.value is None
    chat.turn("yes", device="desktop")
    assert chat.state().project.manuscript_status.value == "completed"


def test_yes_to_another_question_does_not_set_the_stage(chat: _Chat):
    chat.generator.text = "We do. Would you like to see some samples?"
    chat.turn("do you edit thrillers", device="desktop")
    chat.turn("yes", device="desktop")
    assert chat.state().project.manuscript_status.value is None


def test_a_qualified_answer_is_left_to_the_normal_extractors(chat: _Chat):
    chat.generator.text = "Is your manuscript finished?"
    chat.turn("I need help with editing", device="desktop")
    chat.turn("yes but only the first three chapters are polished", device="desktop")
    assert chat.state().project.manuscript_status.value != "completed"
