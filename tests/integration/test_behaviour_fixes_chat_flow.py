"""Regression tests for behaviour bugs found while clearing the failing suite.

Each test pins one fix:
- an explicit refusal to share contact details is remembered;
- a plain timeline question is not vetoed as a declined price;
- a stated time zone is stored and understood;
- "launch date" is a deadline, not a marketing request;
- "am I talking to a real person" is an identity question;
- stated confusion is left unclear; a writing milestone is recognised.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.consultations.service import _normalize_timezone
from bookcraft.components.persona.representative import BookCraftPersona
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings


@pytest.fixture
def client():
    with TestClient(create_app(Settings(app_env="test", api_auth_mode="off"))) as test_client:
        yield test_client


def _chat(client: TestClient, message: str, thread_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id:
        payload["thread_id"] = thread_id
    response = client.post("/api/v1/chat/turn", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    return client.app.state.chat_service.trace_store.for_thread(thread_id)[0]


def _state(client: TestClient, thread_id: str) -> ThreadState:
    return client.app.state.chat_service.threads[UUID(thread_id)].state


# ------------------------------------------------------------------ contact refusal
def test_contact_refusal_is_remembered_across_later_pricing_questions(client: TestClient):
    first = _chat(client, "I need editing for my book.")
    tid = first["thread_id"]
    _chat(client, "How much does editing cost?", tid)
    _chat(client, "I don't want to give you my email or phone. I just want information.", tid)
    assert _state(client, tid).contact_refused is True

    for message in ("Why do you need my contact? Just tell me the price.", "So what does it cost?"):
        _chat(client, message, tid)
        move = (_trace(client, tid).get("lead_objective") or {}).get("objective_move")
        assert move != "ask_contact", f"asked for contact again after a refusal: {message}"


def test_contact_refusal_is_lifted_when_the_visitor_offers_a_detail(client: TestClient):
    first = _chat(client, "I need editing for my book.")
    tid = first["thread_id"]
    _chat(client, "I don't want to share my email or phone.", tid)
    assert _state(client, tid).contact_refused is True
    _chat(client, "Okay, you can reach me at dana@example.com", tid)
    assert _state(client, tid).contact_refused is False


def test_contact_refusal_is_lifted_by_a_consultation_request(client: TestClient):
    first = _chat(client, "I need editing for my book.")
    tid = first["thread_id"]
    _chat(client, "I don't want to share my email or phone.", tid)
    _chat(client, "Actually, I would like to book a consultation.", tid)
    assert _state(client, tid).contact_refused is False


# ------------------------------------------------------------------ timeline veto
def test_plain_timeline_question_keeps_its_intent(client: TestClient):
    body = _chat(client, "How long will ghostwriting take?")
    assert body["intent"]["query_primary"] == "timeline_question"
    corrections = (_trace(client, body["thread_id"]).get("context_arbiter") or {}).get(
        "corrections"
    ) or []
    assert not any("pricing_vetoed" in item for item in corrections)


def test_negated_timeline_is_still_vetoed(client: TestClient):
    body = _chat(client, "I am not asking how long ghostwriting takes.")
    assert body["intent"]["query_primary"] != "timeline_question"


# --------------------------------------------------------------------- time zones
def test_stated_time_zone_is_stored_and_not_asked_again(client: TestClient):
    body = _chat(
        client,
        "My name is Maya Author, my phone is 555-201-3344. "
        "I want a consultation tomorrow at 11 AM Houston time.",
    )
    tid = body["thread_id"]
    assert _state(client, tid).preferred_timezone == "Houston time"
    stage = (_trace(client, tid).get("consultation_state") or {}).get("stage")
    assert stage != "time_captured_needs_timezone"


@pytest.mark.parametrize(
    ("stated", "zone"),
    [
        ("Houston time", "America/Chicago"),
        ("Central time", "America/Chicago"),
        ("Eastern Standard Time", "America/New_York"),
        ("EST", "America/New_York"),
        ("Pakistan time", "Asia/Karachi"),
        ("India time", "Asia/Kolkata"),
        ("BST", "Europe/London"),
        ("America/Denver", "America/Denver"),
    ],
)
def test_time_zone_names_resolve_to_iana_zones(stated: str, zone: str):
    assert _normalize_timezone(stated) == zone


# -------------------------------------------------------------------- launch date
def test_launch_date_is_not_a_marketing_request(client: TestClient):
    body = _chat(
        client,
        "The launch date moved up. I want rush editing, formatting, and publishing.",
    )
    assert body["intent"]["service_primary"] == "editing_proofreading"
    assert "marketing_promotion" not in body["intent"]["service_secondary"]


def test_a_book_launch_is_still_marketing(client: TestClient):
    body = _chat(client, "I need help with my book launch.")
    assert body["intent"]["service_primary"] == "marketing_promotion"


# ----------------------------------------------------------------------- identity
@pytest.mark.parametrize(
    "message",
    [
        "Wait, am I talking to a real person or a bot here?",
        "Am I chatting with a human?",
        "are you a bot",
    ],
)
def test_identity_questions_are_detected(message: str):
    decision = BookCraftPersona().evaluate(message=message, state=ThreadState())
    assert decision.is_identity_question is True


def test_an_ordinary_question_is_not_an_identity_question():
    decision = BookCraftPersona().evaluate(message="How does editing work?", state=ThreadState())
    assert decision.is_identity_question is False


# ------------------------------------------------------------ confusion, milestone
def test_stated_confusion_gets_a_clarifying_plan(client: TestClient):
    first = _chat(client, "What's the best coffee shop in Chicago?")
    tid = first["thread_id"]
    body = _chat(client, "asdf lkjh qwerty I don't even know what I'm asking", tid)
    assert body["intent"]["query_primary"] == "unclear"
    plan = _trace(client, tid).get("response_plan") or {}
    assert plan.get("primary_goal") == "gentle_clarify"


def test_a_real_question_is_not_downgraded_to_unclear(client: TestClient):
    body = _chat(client, "I don't know what editing I need for my novel.")
    assert body["intent"]["query_primary"] != "unclear"


@pytest.mark.parametrize(
    "message",
    ["I just finished the final chapter, it's done!", "I just wrote my first 10,000 words!"],
)
def test_a_writing_milestone_is_recognised(client: TestClient, message: str):
    first = _chat(client, "I need a cover design for my fantasy novel.")
    body = _chat(client, message, first["thread_id"])
    assert "manuscript_status_update" in body["intent"]["query_secondary"]


def test_a_question_about_finishing_is_not_a_milestone(client: TestClient):
    first = _chat(client, "I need a cover design for my fantasy novel.")
    body = _chat(client, "Have you finished the cover for my book?", first["thread_id"])
    assert "manuscript_status_update" not in body["intent"]["query_secondary"]
