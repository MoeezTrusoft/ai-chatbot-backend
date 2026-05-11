from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings
from bookcraft.tools import MemoryAuditSink


def test_pricing_chat_turn_invokes_dispatcher() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={
            "message": "How much does ghostwriting cost for a 50000 word fantasy novel?",
            "correlation_id": "pricing-dispatcher-test",
        },
    )

    assert response.status_code == 200
    audit_sink = app.state.chat_service.tool_dispatcher.audit_sink
    assert isinstance(audit_sink, MemoryAuditSink)
    assert any(
        record["tool_name"] == "pricing.quote.estimate.v2"
        and record["status"] == "succeeded"
        and record["context"]["correlation_id"] == "pricing-dispatcher-test"
        for record in audit_sink.records
    )


def test_portfolio_chat_turn_invokes_dispatcher() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={
            "message": "Show me cover design portfolio samples for a romance book.",
            "correlation_id": "portfolio-dispatcher-test",
        },
    )

    assert response.status_code == 200
    audit_sink = app.state.chat_service.tool_dispatcher.audit_sink
    assert isinstance(audit_sink, MemoryAuditSink)
    assert any(
        record["tool_name"] == "portfolio.request_samples.v1"
        and record["status"] == "succeeded"
        and record["context"]["correlation_id"] == "portfolio-dispatcher-test"
        for record in audit_sink.records
    )
