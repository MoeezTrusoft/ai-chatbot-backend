from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def _chat(client: TestClient, message: str, *, thread_id: object | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows
    return rows[0]


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


def test_service_request_asks_contact_instead_of_over_discovery() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(client, "I need editing for my completed fantasy novel")
        first = _trace(client, body["thread_id"])
        _chat(client, "I want to hire an editor for it", thread_id=body["thread_id"])
        second = _trace(client, body["thread_id"])

    # First informational message: welcome and engage (no contact ask yet), but no
    # scoping either. The next step offered is a consultation or contact, never a
    # word count or genre question.
    assert (first.get("response_plan") or {}).get("next_question") in {
        "name_and_email_or_phone",
        "consultation_interest",
    }
    # Once the visitor shows buying intent, discovery stops and contact is requested.
    ro = second.get("lead_objective") or {}
    rp = second.get("response_plan") or {}
    assert ro.get("stop_discovery") is True
    assert rp.get("next_question") in {"name_and_email_or_phone", "consultation_interest"}


def test_name_plus_email_creates_lead() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        r1 = _chat(client, "I need editing for my completed fantasy novel")
        thread_id = r1["thread_id"]
        _chat(
            client,
            "My name is Sarah Khan and my email is sarah@example.com",
            thread_id=thread_id,
        )
        t = _trace(client, thread_id)

    assert (t.get("contact_capture") or {}).get("lead_contact_ready") is True
    assert (t.get("action_plan") or {}).get("action_type") == "create_lead"
    assert t.get("lead_created") is True


def test_name_plus_phone_creates_lead() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        r1 = _chat(client, "I need editing for my completed fantasy novel")
        thread_id = r1["thread_id"]
        _chat(client, "My name is Sarah Khan and my phone is +1 555 123 4567", thread_id=thread_id)
        t = _trace(client, thread_id)

    assert (t.get("contact_capture") or {}).get("lead_contact_ready") is True
    assert t.get("lead_created") is True


def test_lead_created_response_has_no_more_discovery_question() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        r1 = _chat(client, "I need editing for my completed fantasy novel")
        thread_id = r1["thread_id"]
        r2 = _chat(
            client,
            "My name is Sarah Khan and my email is sarah@example.com",
            thread_id=thread_id,
        )
        t = _trace(client, thread_id)

    assert (t.get("response_plan") or {}).get("primary_goal") == "lead_created_confirmation"
    # No discovery question follows. The one thing the bot may still ask for is the
    # phone number: a consultation can only be booked with one.
    assert (t.get("response_plan") or {}).get("next_question") in {None, "missing_phone"}
    assert "word count" not in _text(r2).lower()


def test_intake_form_rich_segment_present_when_contact_missing() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(client, "I want to hire a ghostwriter for my memoir")

    segments = []
    for bubble in body.get("bubbles", []):
        segments.extend(bubble.get("rich_segments", []))
    assert any(seg.get("type") == "lead_intake_form" for seg in segments)


def test_intake_form_waits_while_a_question_is_being_answered() -> None:
    """A pricing question is answered first; the contact form is not pushed with it."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(client, "How much does ghostwriting cost?")
        t = _trace(client, body["thread_id"])

    segments = []
    for bubble in body.get("bubbles", []):
        segments.extend(bubble.get("rich_segments", []))
    assert not any(seg.get("type") == "lead_intake_form" for seg in segments)
    assert (t.get("answer_before_capture") or {}).get("suppress_contact_until_answered") is True


def test_claude_only_contract_holds() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(client, "How much does ghostwriting cost?")
        t = _trace(client, body["thread_id"])

    contract = t.get("customer_response_contract") or {}
    assert contract.get("final_responder") == "claude_required"
