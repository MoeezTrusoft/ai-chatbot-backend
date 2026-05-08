from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_healthz_returns_process_health() -> None:
    app = create_app(Settings(app_env="test", readiness_check_externals=False))

    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "bookcraft-chatbot",
        "environment": "test",
    }


def test_readyz_skips_external_checks_by_default() -> None:
    app = create_app(Settings(app_env="test", readiness_check_externals=False))

    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["dependencies"]["externals"]["status"] == "skipped"


def test_metrics_endpoint_exposes_prometheus_payload() -> None:
    app = create_app(Settings(app_env="test", readiness_check_externals=False))

    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert "chatbot_http_requests_total" in response.text


def test_correlation_id_header_is_preserved() -> None:
    app = create_app(Settings(app_env="test", readiness_check_externals=False))

    response = TestClient(app).get("/healthz", headers={"x-correlation-id": "corr-test"})

    assert response.headers["x-correlation-id"] == "corr-test"
