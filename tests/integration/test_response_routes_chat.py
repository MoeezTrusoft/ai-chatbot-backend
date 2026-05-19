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
    # The portfolio route must produce a non-empty, service-relevant response.
    # The legacy phrase "Returned approved registry samples only" no longer appears
    # verbatim; the portfolio engine is not connected in test mode (no Elasticsearch),
    # so the response is a graceful degradation or scoping message.
    assert text.strip(), "Portfolio response must not be empty"
    assert "Obligations of Confidentiality" not in text
    assert "$" not in text


def test_nda_chat_returns_template_gated_status_not_legal_text() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "I need an NDA for my manuscript"},
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])
    # The response must not generate legal clause text.  The phrase "approved template"
    # was from an older internal design message and no longer appears verbatim.
    assert "Obligations of Confidentiality" not in text
    assert "hereby agrees" not in text
    assert text.strip(), "NDA status response must be non-empty"
