from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_cors_allows_configured_origin() -> None:
    app = create_app(
        Settings(
            app_env="test",
            ws_allowed_origins="http://localhost:3000,http://localhost:8000",
        )
    )
    client = TestClient(app)

    response = client.options(
        "/api/v1/chat/turn",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_unconfigured_origin() -> None:
    app = create_app(
        Settings(
            app_env="test",
            ws_allowed_origins="http://localhost:3000",
        )
    )
    client = TestClient(app)

    response = client.options(
        "/api/v1/chat/turn",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 400


def test_websocket_allows_configured_origin() -> None:
    app = create_app(
        Settings(
            app_env="dev",
            ws_allowed_origins="http://localhost:3000",
        )
    )
    client = TestClient(app)

    with client.websocket_connect(
        f"/api/v1/chat/ws/{uuid4()}",
        headers={"Origin": "http://localhost:3000"},
    ) as websocket:
        websocket.send_json({"message": "hello"})
        received = websocket.receive_json()
        assert received["type"] in {"typing_start", "message_bubble", "turn_complete"}


def test_websocket_rejects_unconfigured_origin() -> None:
    app = create_app(
        Settings(
            app_env="dev",
            ws_allowed_origins="http://localhost:3000",
        )
    )
    client = TestClient(app)

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/api/v1/chat/ws/{uuid4()}",
            headers={"Origin": "https://evil.example"},
        ):
            pass