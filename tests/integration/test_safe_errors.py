from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_unhandled_exception_returns_safe_error_response() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    response = client.get(
        "/_test/crash",
        headers={"X-Correlation-ID": "safe-error-test"},
    )

    assert response.status_code == 500
    assert response.headers["x-correlation-id"] == "safe-error-test"
    assert response.json() == {
        "error": "internal_error",
        "correlation_id": "safe-error-test",
    }


def test_unhandled_exception_response_does_not_leak_details_or_pii() -> None:
    app = create_app(Settings(app_env="test"))
    client = TestClient(app)

    response = client.get("/_test/crash")

    body = response.text
    assert "boom" not in body
    assert "author@example.com" not in body
    assert "+92 300 1234567" not in body
    assert "internal_error" in body
