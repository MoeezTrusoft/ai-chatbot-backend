import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings

SIGNING_KEY = "integration-test-signing-key"  # noqa: S105


def make_token(claims: dict[str, object], key: str = SIGNING_KEY) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    raw_header = b64(json.dumps(header, separators=(",", ":")).encode())
    raw_payload = b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{raw_header}.{raw_payload}"
    digest = hmac.new(key.encode(), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{b64(digest)}"


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def auth_settings() -> Settings:
    return Settings(
        app_env="test",
        api_auth_mode="jwt",
        jwt_signing_key=SIGNING_KEY,
        ws_allowed_origins="http://localhost:3000",
    )


def test_chat_turn_allows_auth_off_by_default() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    client = TestClient(app)

    response = client.post("/api/v1/chat/turn", json={"message": "hello"})

    assert response.status_code == 200


def test_chat_turn_rejects_missing_bearer_token_when_jwt_enabled() -> None:
    app = create_app(auth_settings())
    client = TestClient(app)

    response = client.post("/api/v1/chat/turn", json={"message": "hello"})

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "unauthorized"


def test_chat_turn_accepts_valid_bearer_token_when_jwt_enabled() -> None:
    token = make_token({"sub": "customer@example.com", "exp": int(time.time()) + 3600})
    app = create_app(auth_settings())
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat/turn",
        json={"message": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_websocket_rejects_missing_token_when_jwt_enabled() -> None:
    app = create_app(auth_settings())
    client = TestClient(app)

    with pytest_raises_ws_disconnect():
        with client.websocket_connect(
            f"/api/v1/chat/ws/{uuid4()}",
            headers={"Origin": "http://localhost:3000"},
        ):
            pass


def test_websocket_accepts_access_token_when_jwt_enabled() -> None:
    token = make_token({"sub": "customer@example.com", "exp": int(time.time()) + 3600})
    app = create_app(auth_settings())
    client = TestClient(app)

    with client.websocket_connect(
        f"/api/v1/chat/ws/{uuid4()}?access_token={token}",
        headers={"Origin": "http://localhost:3000"},
    ) as websocket:
        websocket.send_json({"message": "hello"})
        frames = []
        while True:
            frame = websocket.receive_json()
            frames.append(frame["type"])
            if frame["type"] == "turn_complete":
                break

    assert "turn_complete" in frames


class pytest_raises_ws_disconnect:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None
        assert issubclass(exc_type, WebSocketDisconnect)
        return True
