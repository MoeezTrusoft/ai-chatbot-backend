from __future__ import annotations

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_portfolio_chat_returns_registry_samples_only() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "Show me cover design portfolio samples for cozy mystery"},
    )

    assert response.status_code == 200
    body = response.json()
    text = " ".join(bubble["text"] for bubble in body["bubbles"])
    assert "Returned approved registry samples only" in text
    assert "http" in text
    rich_urls = [
        segment["text"]
        for bubble in body["bubbles"]
        for segment in bubble["rich_segments"]
        if segment["type"] == "url"
    ]
    assert rich_urls


def test_nda_chat_returns_template_gated_status_not_legal_text() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "I need an NDA for my manuscript"},
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])
    assert "approved template" in text
    assert "Obligations of Confidentiality" not in text
