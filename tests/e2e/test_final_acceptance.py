from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.documents import (
    AgreementParams,
    DocumentEngine,
    DocumentStatus,
    DocumentTemplateRegistry,
    NDAParams,
    register_document_tools,
)
from bookcraft.components.response.schemas import FormattedBubble
from bookcraft.domain.enums import ToolInvocationStatus
from bookcraft.infra.cache import CacheKeyBuilder
from bookcraft.infra.config import Settings
from bookcraft.tools import (
    IdempotencyStore,
    MemoryAuditSink,
    MemoryCache,
    ToolContext,
    ToolDispatcher,
    ToolRegistry,
)
from bookcraft.tools.gating import ToolGatingPolicy


@pytest.fixture()
def client() -> TestClient:
    app = create_app(Settings(app_env="test"))
    return TestClient(app)


def test_phase_14_customer_journey_acceptance(client: TestClient) -> None:
    first = _chat(client, "I am looking for ghostwriting help for a fantasy novel.")
    thread_id = UUID(first["thread_id"])
    assert first["language_status"] == "en"
    assert _joined_text(first["bubbles"])
    assert first["intent"]["query_primary"] in {"service_question", "unclear"}

    quote_missing = _chat(
        client,
        "How much will it cost and how long will it take?",
        thread_id=thread_id,
    )
    quote_missing_text = _joined_text(quote_missing["bubbles"])
    assert any(term in quote_missing_text.lower() for term in ["service", "words", "pages"])
    assert "$" not in quote_missing_text

    quote_gated = _chat(
        client,
        "Ghostwriting for a 50000 word fantasy manuscript. Please price it and timeline it.",
        thread_id=thread_id,
    )
    quote_gated_text = _joined_text(quote_gated["bubbles"])
    assert any(
        phrase in quote_gated_text
        for phrase in ["pricing values are not approved", "timeline values are not approved"]
    )
    assert "won't guess at" in quote_gated_text
    assert "$" not in quote_gated_text

    portfolio = _chat(
        client,
        "Show me cover design portfolio samples for fantasy books.",
        thread_id=thread_id,
    )
    portfolio_text = _joined_text(portfolio["bubbles"])
    assert "Returned approved registry samples only" in portfolio_text
    assert _approved_urls(portfolio["bubbles"])

    ghostwriting_portfolio = _chat(
        client,
        "Show me ghostwriting samples.",
        thread_id=thread_id,
    )
    assert "confidential" in _joined_text(ghostwriting_portfolio["bubbles"]).lower()

    nda = _chat(client, "I need an NDA for my manuscript.", thread_id=thread_id)
    nda_text = _joined_text(nda["bubbles"])
    assert "approved template" in nda_text
    assert "Obligations of Confidentiality" not in nda_text

    agreement = _chat(client, "I need a service agreement.", thread_id=thread_id)
    agreement_text = _joined_text(agreement["bubbles"])
    assert "approved template" in agreement_text
    assert "fee fields must come from an accepted deterministic quote" in agreement_text

    service = client.app.state.chat_service
    memory = service.threads[thread_id]
    event_types = [event["event_type"] for event in memory.events]
    assert "trimatch.voted" in event_types
    assert "intent.classified" in event_types
    assert "assistant.response" in event_types
    assert all(event["event_hash"] for event in memory.events)

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    metrics_text = metrics.text
    assert "chatbot_turns_total" in metrics_text
    assert "llm_calls_total" in metrics_text
    assert "trimatch_votes_total" in metrics_text
    assert "portfolio_requests_total" in metrics_text


def test_phase_14_document_generation_and_tool_audit(tmp_path: Path) -> None:
    engine = DocumentEngine(
        registry=DocumentTemplateRegistry("data/templates"),
        output_dir=tmp_path,
        pdf_rendering_enabled=False,
    )

    nda = engine.generate_nda(_nda_params())
    assert nda.status == DocumentStatus.VERIFIED
    assert nda.template_version == "nda_v1"
    assert nda.parameter_hash != nda.rendered_hash
    assert nda.html_path is not None
    nda_html = Path(nda.html_path).read_text(encoding="utf-8")
    assert "Avery Author" in nda_html
    assert "<%" not in nda_html

    agreement = engine.generate_agreement(_agreement_params())
    assert agreement.status == DocumentStatus.VERIFIED
    assert agreement.template_version == "service_agreement_v1"
    assert agreement.parameter_hash != agreement.rendered_hash
    assert agreement.html_path is not None
    agreement_html = Path(agreement.html_path).read_text(encoding="utf-8")
    assert "Editing &amp; Proofreading" in agreement_html
    assert "<%" not in agreement_html

    audit_sink = _invoke_document_tool(engine, tmp_path)
    assert audit_sink.records
    assert audit_sink.records[0]["status"] == ToolInvocationStatus.SUCCEEDED.value
    assert audit_sink.records[0]["tool_name"] == "documents.generate_nda.v1"


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: UUID | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    response = client.post("/api/v1/chat/turn", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _joined_text(bubbles: object) -> str:
    return " ".join(bubble["text"] for bubble in bubbles)  # type: ignore[index]


def _approved_urls(bubbles: object) -> list[str]:
    return [
        segment["text"]
        for bubble in bubbles  # type: ignore[union-attr]
        for segment in FormattedBubble.model_validate(bubble).rich_segments
        if segment["type"] == "url"
    ]


def _nda_params() -> NDAParams:
    return NDAParams.model_validate(
        {
            "date": "May 8, 2026",
            "authorTitle": "Ms.",
            "authorFullName": "Avery Author",
            "authorPhone": "555-0100",
            "authorEmail": "avery@example.com",
            "signature": "Jerry Miller",
        }
    )


def _agreement_params() -> AgreementParams:
    return AgreementParams.model_validate(
        {
            "logoPath": "",
            "effectiveDate": "May 8, 2026",
            "abbreviation": "Ms.",
            "clientFullName": "Avery Author",
            "clientPhone": "555-0100",
            "clientEmail": "avery@example.com",
            "clientLocation": "Houston, Texas",
            "filteredServices": [
                {
                    "title": "Editing & Proofreading",
                    "items": [
                        {
                            "title": "Copy Editing",
                            "description": "Line-level review from the selected service scope.",
                        }
                    ],
                }
            ],
            "finalFee": "fixture-engine-output",
            "totalFee": "fixture-engine-output",
            "discountPercent": 0,
            "scheduleType": "Engine-approved payment schedule",
            "signature": "Jerry Miller",
            "agreementDate": "May 8, 2026",
        }
    )


def _invoke_document_tool(engine: DocumentEngine, tmp_path: Path) -> MemoryAuditSink:
    del tmp_path

    async def invoke() -> MemoryAuditSink:
        registry = ToolRegistry()
        register_document_tools(registry, engine)
        audit_sink = MemoryAuditSink()
        dispatcher = ToolDispatcher(
            registry=registry,
            idempotency_store=IdempotencyStore(
                client=MemoryCache(),
                keys=CacheKeyBuilder(environment="test"),
                ttl_seconds=60,
            ),
            audit_sink=audit_sink,
            gating_policy=ToolGatingPolicy(
                nda_mode="verifier_gated",
                agreement_mode="verifier_gated",
            ),
        )
        await dispatcher.invoke(
            tool_name="documents.generate_nda.v1",
            raw_input={"params": _nda_params().model_dump(by_alias=True, mode="json")},
            context=ToolContext(
                thread_id=uuid4(),
                customer_id=None,
                turn_sequence=1,
                invoked_by="phase14_acceptance",
                correlation_id="phase14-doc-tool",
                idempotency_key="phase14-nda-1",
                environment="test",
            ),
        )
        return audit_sink

    import anyio

    return anyio.run(invoke)
