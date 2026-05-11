from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_pricing_request_without_service_asks_for_service() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "How much does it cost?"},
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])
    assert "Which BookCraft service" in text


def test_pricing_request_with_missing_sizing_asks_for_word_count() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "How much does ghostwriting cost for fantasy?"},
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])
    assert "how many words" in text.lower()
    assert "$" not in text


def test_pricing_request_with_hidden_defaults_asks_for_confirmation() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "How much does ghostwriting cost for 50000 words fantasy?"},
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])
    lowered = text.lower()
    assert "$" not in text
    assert "confirm" in lowered
    assert "hidden assumptions" in lowered
    assert "ghostwriting scope" in lowered
    assert "manuscript status" in lowered
