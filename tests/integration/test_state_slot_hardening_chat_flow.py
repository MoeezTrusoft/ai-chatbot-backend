from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_fake_name_not_used_in_consultation() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "I am writing a memoir and want a consultation.")
        memory = _thread_memory(client, body)

    action = _sales_action(memory)
    text = _joined_text(body).casefold()

    assert memory.state.personal.name.value is None
    assert "writing a memoir" not in action["collected_slots"]
    assert "writing a memoir" not in text
    assert action["action_type"] == "schedule_consultation"
    assert action["status"] == "missing_info"
    assert "name" in action["missing_slots"]
    assert "email_or_phone" in action["missing_slots"]


def test_real_name_used_in_consultation() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "My name is Maya Author, my email is maya@example.com. "
            "I want a consultation tomorrow at 11 AM Houston time.",
        )
        memory = _thread_memory(client, body)

    action = _sales_action(memory)
    pending_slot = memory.state.sales_actions.consultation.pending_slot

    assert action["action_type"] == "schedule_consultation"
    assert action["status"] == "needs_confirmation"
    assert action["collected_slots"]["name"] == "Maya Author"
    assert pending_slot is not None
    assert pending_slot["name"] == "Maya Author"
    assert pending_slot["email"] == "maya@example.com"
    assert memory.state.personal.email.value == "maya@example.com"


def test_vague_deadline_not_used_for_quote() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "I need editing. Deadline is whenever I'm ready.")
        memory = _thread_memory(client, body)

    text = _joined_text(body).casefold()

    assert memory.state.project.target_completion_date.value is None
    assert "whenever i'm ready" not in str(memory.state.project.target_completion_date.value)
    assert "deadline" in text or "manuscript stage" in text


def test_send_pricing_details_does_not_confirm_booking() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(
            client,
            "My name is Maya Author, my email is maya@example.com. "
            "I want a consultation tomorrow at 11 AM Houston time.",
        )
        thread_id = first["thread_id"]
        _chat(client, "send me pricing details", thread_id=thread_id)
        memory = _thread_memory(client, first)

    assert memory.state.sales_actions.pending_confirmation.type == "schedule_consultation"
    assert memory.state.sales_actions.consultation.confirmed_appointment_id is None
    assert _last_sales_action(memory)["action_type"] == "price_quote"


def test_durable_state_not_overwritten_by_weak_later_phrase() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I have finished my manuscript.")
        thread_id = first["thread_id"]
        _chat(client, "Starting from scratch would be hard.", thread_id=thread_id)
        memory = _thread_memory(client, first)

    assert memory.state.project.manuscript_status.value == "completed_draft"


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: object | None = None,
) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    response = client.post("/api/v1/chat/turn", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _joined_text(body: dict[str, Any]) -> str:
    return " ".join(str(bubble["text"]) for bubble in body["bubbles"])


def _thread_memory(client: TestClient, body: dict[str, Any]) -> Any:
    return client.app.state.chat_service.threads[UUID(body["thread_id"])]


def _sales_action(memory: Any) -> dict[str, Any]:
    for event in memory.events:
        if event["event_type"] == "sales_action.planned":
            payload = event["payload"]
            assert isinstance(payload, dict)
            return payload
    raise AssertionError("sales_action.planned event was not recorded")


def _last_sales_action(memory: Any) -> dict[str, Any]:
    for event in reversed(memory.events):
        if event["event_type"] == "sales_action.planned":
            payload = event["payload"]
            assert isinstance(payload, dict)
            return payload
    raise AssertionError("sales_action.planned event was not recorded")
