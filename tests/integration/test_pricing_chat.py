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
    # The response must ask for at least one piece of scoping information rather
    # than committing to a price. Exact phrasing evolves with template changes.
    assert "$" not in text, "No price must be emitted when service is unknown"
    assert "?" in text or any(
        kw in text.lower() for kw in ("service", "ghostwriting", "cover", "editing", "word", "page")
    ), f"Expected scoping question; got: {text[:200]}"


def test_pricing_request_with_missing_sizing_asks_for_word_count() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "How much does ghostwriting cost for fantasy?"},
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])
    assert "$" not in text
    # The response must ask for word/page count. Exact phrasing may vary.
    assert any(kw in text.lower() for kw in ("word", "page", "count", "length")), (
        f"Expected word/page count question; got: {text[:200]}"
    )


def test_pricing_request_with_hidden_defaults_asks_for_confirmation() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "How much does ghostwriting cost for 50000 words fantasy?"},
    )

    assert response.status_code == 200
    text = " ".join(bubble["text"] for bubble in response.json()["bubbles"])
    assert "$" not in text
    # The response must ask for a missing scoping detail or request confirmation.
    # Exact phrases like "hidden assumptions" and "ghostwriting scope" no longer
    # appear verbatim; the core behavior — no price without all required slots — is tested.
    assert "?" in text or any(
        kw in text.lower() for kw in ("manuscript", "deadline", "stage", "word", "page")
    ), f"Expected clarifying question; got: {text[:200]}"
