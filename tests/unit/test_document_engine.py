from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from bookcraft.components.documents import (
    AgreementParams,
    DocumentEngine,
    DocumentStatus,
    DocumentTemplateRegistry,
    NDAParams,
    TemplateVerifier,
    register_document_tools,
)
from bookcraft.infra.cache import CacheKeyBuilder
from bookcraft.tools import (
    IdempotencyStore,
    MemoryAuditSink,
    MemoryCache,
    ToolContext,
    ToolDispatcher,
    ToolRegistry,
)
from bookcraft.tools.gating import ToolGatingPolicy


def nda_params() -> NDAParams:
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


def agreement_params() -> AgreementParams:
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
            "finalFee": "1000",
            "totalFee": "1000",
            "discountPercent": 0,
            "scheduleType": "100% upon signing",
            "signature": "Jerry Miller",
            "agreementDate": "May 8, 2026",
        }
    )


def engine(tmp_path) -> DocumentEngine:  # type: ignore[no-untyped-def]
    return DocumentEngine(
        registry=DocumentTemplateRegistry("data/templates"),
        output_dir=tmp_path,
        pdf_rendering_enabled=False,
    )


def test_template_verifier_accepts_approved_templates() -> None:
    errors = TemplateVerifier(DocumentTemplateRegistry("data/templates")).verify_all()

    assert errors == []


def test_nda_render_uses_template_fields_and_hashes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = engine(tmp_path).generate_nda(nda_params())

    assert result.status == DocumentStatus.VERIFIED
    assert result.template_version == "nda_v1"
    assert result.parameter_hash != result.rendered_hash
    assert result.html_path is not None
    html = open(result.html_path, encoding="utf-8").read()
    assert "Avery Author" in html
    assert "<%" not in html


def test_agreement_render_supports_selected_services(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = engine(tmp_path).generate_agreement(agreement_params())

    assert result.status == DocumentStatus.VERIFIED
    assert result.template_version == "service_agreement_v1"
    assert result.html_path is not None
    html = open(result.html_path, encoding="utf-8").read()
    assert "Editing &amp; Proofreading" in html
    assert "Copy Editing" in html
    assert "<%" not in html


def test_missing_required_nda_field_fails_validation() -> None:
    with pytest.raises(ValidationError):
        NDAParams.model_validate(
            {
                "date": "May 8, 2026",
                "authorTitle": "Ms.",
                "authorFullName": "Avery Author",
                "authorPhone": "555-0100",
                "signature": "Jerry Miller",
            }
        )


@pytest.mark.asyncio
async def test_document_tool_defers_in_manual_mode(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = ToolRegistry()
    register_document_tools(registry, engine(tmp_path))
    audit_sink = MemoryAuditSink()
    dispatcher = ToolDispatcher(
        registry=registry,
        idempotency_store=IdempotencyStore(
            client=MemoryCache(),
            keys=CacheKeyBuilder(environment="test"),
            ttl_seconds=60,
        ),
        audit_sink=audit_sink,
        gating_policy=ToolGatingPolicy(nda_mode="manual", agreement_mode="manual"),
    )

    result = await dispatcher.invoke(
        tool_name="documents.generate_nda.v1",
        raw_input={"params": nda_params().model_dump(by_alias=True, mode="json")},
        context=ToolContext(
            thread_id=uuid4(),
            customer_id=None,
            turn_sequence=1,
            invoked_by="test",
            correlation_id="corr-1",
            idempotency_key="doc-1",
            environment="test",
        ),
    )

    assert result.status == "deferred"
    assert audit_sink.records[0]["status"] == "deferred"
