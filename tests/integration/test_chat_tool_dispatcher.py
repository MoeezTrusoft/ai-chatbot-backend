from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.domain.enums import ServiceCategory
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings
from bookcraft.tools import MemoryAuditSink


@pytest.mark.asyncio
async def test_pricing_turn_invokes_dispatcher_after_assumptions_are_explicit() -> None:
    app = create_app(Settings(app_env="test"))
    service = app.state.chat_service

    state = ThreadState()
    state.project.word_count = FieldMeta[int](
        value=50000,
        confidence=0.95,
        source="user_stated",
        raw_excerpt="50000 words",
    )

    quote, timeline, question = await service._price_turn(
        thread_id=uuid4(),
        customer_id=None,
        turn_sequence=1,
        correlation_id="pricing-dispatcher-test",
        state=state,
        intent_service=ServiceCategory.GHOSTWRITING,
        message=(
            "How much for full ghostwriting from scratch for a fantasy novel, "
            "50000 words, outline ready?"
        ),
        confidence=0.95,
    )

    assert quote is not None
    assert timeline is None
    assert question is None

    audit_sink = service.tool_dispatcher.audit_sink
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
