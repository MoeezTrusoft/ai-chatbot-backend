from uuid import uuid4

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_http_chat_turn_rate_limit_blocks_after_limit() -> None:
    app = create_app(
        Settings(
            app_env="test",
            rate_limit_per_ip_per_minute=1,
        )
    )
    client = TestClient(app)

    first = client.post(
        "/api/v1/chat/turn",
        json={"message": "hello", "correlation_id": "rate-limit-1"},
    )
    second = client.post(
        "/api/v1/chat/turn",
        json={"message": "hello again", "correlation_id": "rate-limit-2"},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["error"] == "rate_limited"
    assert "Retry-After" in second.headers


def test_websocket_message_rate_limit_returns_error_frame() -> None:
    app = create_app(
        Settings(
            app_env="dev",
            ws_allowed_origins="http://localhost:3000",
            rate_limit_per_ip_per_minute=1,
        )
    )
    client = TestClient(app)

    with client.websocket_connect(
        f"/api/v1/chat/ws/{uuid4()}",
        headers={"Origin": "http://localhost:3000"},
    ) as websocket:
        websocket.send_json({"message": "hello"})
        # Drain first turn until turn_complete.
        while True:
            frame = websocket.receive_json()
            if frame["type"] == "turn_complete":
                break

        websocket.send_json({"message": "second message"})
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert frame["code"] == "rate_limited"
    assert frame["retry_after_seconds"] >= 1