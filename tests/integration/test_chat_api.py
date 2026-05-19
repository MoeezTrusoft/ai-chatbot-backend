from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_chat_turn_greeting_shortcut() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post("/api/v1/chat/turn", json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["bubbles"][0]["text"] == "Hello! How can I help with your book project today?"
    assert body["intent"]["query_primary"] == "greeting"


def test_chat_turn_pricing_does_not_emit_numbers() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "How much does ghostwriting cost?"},
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])
    assert "$" not in text
    # The response must ask for at least one missing scoping detail rather than
    # emitting any price figure.  The phrase "deterministic quote engine" was from
    # an old internal design and no longer appears in customer-facing responses.
    assert "?" in text or any(
        kw in text.lower() for kw in ("word", "page", "genre", "manuscript", "deadline")
    ), f"Expected a scoping question in response; got: {text[:200]}"


def test_chat_turn_extracts_contact_into_thread_state() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)
    response = client.post(
        "/api/v1/chat/turn",
        json={"message": "email me at author@example.com"},
    )

    assert response.status_code == 200
    thread_id = response.json()["thread_id"]
    state = app.state.chat_service.threads[UUID(thread_id)].state
    assert state.personal.email.value == "author@example.com"


def test_websocket_chat_smoke() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    with client.websocket_connect(f"/api/v1/chat/ws/{uuid4()}") as websocket:
        websocket.send_json({"message": "hello"})
        event_types = [websocket.receive_json()["type"] for _ in range(4)]

    assert "message_bubble" in event_types
    assert "turn_complete" in event_types
