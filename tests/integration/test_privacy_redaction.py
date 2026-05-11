from uuid import UUID

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_thread_event_redacts_user_message_in_memory() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat/turn",
        json={
            "message": "Hi, my email is author@example.com and phone is +92 300 1234567",
            "correlation_id": "privacy-event-test",
        },
    )

    assert response.status_code == 200
    thread_id = UUID(response.json()["thread_id"])
    memory = app.state.chat_service.threads[thread_id]
    serialized_events = str(memory.events)

    assert "author@example.com" not in serialized_events
    assert "+92 300 1234567" not in serialized_events
    assert "[REDACTED_EMAIL]" in serialized_events
    assert "[REDACTED_PHONE]" in serialized_events


def test_tool_audit_redacts_params_in_memory_sink() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat/turn",
        json={
            "message": (
                "How much does ghostwriting cost for 50000 words? My email is author@example.com"
            ),
            "correlation_id": "privacy-audit-test",
        },
    )

    assert response.status_code == 200
    records = app.state.chat_service.tool_dispatcher.audit_sink.records
    serialized_records = str(records)

    assert "author@example.com" not in serialized_records
