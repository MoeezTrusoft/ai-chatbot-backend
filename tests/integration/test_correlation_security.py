from uuid import UUID

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_http_correlation_header_rejects_injection() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    response = client.get(
        "/healthz",
        headers={"X-Correlation-ID": "<script>alert(1)</script>"},
    )

    assert response.status_code == 200
    returned = response.headers["x-correlation-id"]
    assert returned != "<script>alert(1)</script>"
    UUID(returned)


def test_chat_payload_correlation_id_is_sanitized() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat/turn",
        json={
            "message": "hello",
            "correlation_id": "<script>alert(1)</script>",
        },
    )

    assert response.status_code == 200
