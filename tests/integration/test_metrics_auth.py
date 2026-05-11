from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings

VALID_METRICS_TOKEN = "secret-token"  # noqa: S105


def test_metrics_allowed_in_test_env() -> None:
    app = create_app(Settings(app_env="test", metrics_public=False))
    client = TestClient(app)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "chatbot_http_requests_total" in response.text


def test_metrics_forbidden_without_token_outside_test() -> None:
    app = create_app(
        Settings(
            app_env="dev",
            metrics_public=False,
            metrics_bearer_token=VALID_METRICS_TOKEN,
        )
    )
    client = TestClient(app)

    response = client.get("/metrics")

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "metrics_forbidden"


def test_metrics_allowed_with_valid_bearer_token() -> None:
    app = create_app(
        Settings(
            app_env="dev",
            metrics_public=False,
            metrics_bearer_token=VALID_METRICS_TOKEN,
        )
    )
    client = TestClient(app)

    response = client.get(
        "/metrics",
        headers={"Authorization": f"Bearer {VALID_METRICS_TOKEN}"},
    )

    assert response.status_code == 200
    assert "chatbot_http_requests_total" in response.text


def test_metrics_forbidden_with_wrong_bearer_token() -> None:
    app = create_app(
        Settings(
            app_env="dev",
            metrics_public=False,
            metrics_bearer_token=VALID_METRICS_TOKEN,
        )
    )
    client = TestClient(app)

    response = client.get(
        "/metrics",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 403
