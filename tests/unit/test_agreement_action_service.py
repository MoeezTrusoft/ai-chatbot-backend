from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from bookcraft.components.document_actions import (
    AgreementActionRequest,
    AgreementActionService,
)
from bookcraft.components.document_actions.repository import (
    InMemoryDocumentRequestRepository,
)
from bookcraft.components.documents.engine import DocumentEngine
from bookcraft.components.documents.registry import DocumentTemplateRegistry
from bookcraft.components.storage.models import SalesPricingQuoteRecord


@pytest.fixture()
def document_engine(tmp_path: Path) -> DocumentEngine:
    return DocumentEngine(
        registry=DocumentTemplateRegistry("data/templates"),
        output_dir=tmp_path,
        pdf_rendering_enabled=False,
    )


@pytest.mark.asyncio
async def test_agreement_action_requires_quote(
    document_engine: DocumentEngine,
) -> None:
    service = AgreementActionService(
        document_engine=document_engine,
        repository=InMemoryDocumentRequestRepository(),
        email_client=None,
    )

    with pytest.raises(ValueError, match="agreement_requires_existing_quote"):
        await service.generate_and_maybe_send(
            AgreementActionRequest(
                customer_id=uuid4(),
                thread_id=uuid4(),
                client_full_name="Maya Author",
                client_phone="+1 555 123 4567",
                client_email="maya@example.com",
                client_location="Houston, TX",
                effective_date="2026-05-18",
            )
        )


@pytest.mark.asyncio
async def test_agreement_action_generates_from_quote(
    document_engine: DocumentEngine,
) -> None:
    repository = InMemoryDocumentRequestRepository()
    thread_id = uuid4()
    customer_id = uuid4()
    quote_id = uuid4()
    repository.quote_records.append(
        SalesPricingQuoteRecord(
            quote_id=quote_id,
            customer_id=customer_id,
            thread_id=thread_id,
            services=["editing_proofreading"],
            input_params={},
            used_default_assumptions=False,
            assumptions=None,
            quote_output={"total": "$1,250.00"},
            customer_safe_summary="Estimated quote is $1,250.",
            status="created",
        )
    )

    service = AgreementActionService(
        document_engine=document_engine,
        repository=repository,
        email_client=None,
    )

    result = await service.generate_and_maybe_send(
        AgreementActionRequest(
            customer_id=customer_id,
            thread_id=thread_id,
            quote_id=quote_id,
            client_full_name="Maya Author",
            client_phone="+1 555 123 4567",
            client_email="maya@example.com",
            client_location="Houston, TX",
            effective_date="2026-05-18",
            send_email=False,
        )
    )

    assert result.document_id is not None
    assert result.quote_id == quote_id
    assert result.status in {"verified", "generated"}
    assert repository.records


@pytest.mark.asyncio
async def test_agreement_action_rejects_zero_total_quote(
    document_engine: DocumentEngine,
) -> None:
    repository = InMemoryDocumentRequestRepository()
    thread_id = uuid4()
    customer_id = uuid4()
    quote_id = uuid4()
    repository.quote_records.append(
        SalesPricingQuoteRecord(
            quote_id=quote_id,
            customer_id=customer_id,
            thread_id=thread_id,
            services=["editing_proofreading"],
            input_params={},
            used_default_assumptions=False,
            assumptions=None,
            quote_output={
                "total_price_range": {
                    "low": {"amount": "0.00", "currency": "USD"},
                    "high": {"amount": "0.00", "currency": "USD"},
                }
            },
            customer_safe_summary="Quote needs clarification.",
            status="needs_clarification",
        )
    )

    service = AgreementActionService(
        document_engine=document_engine,
        repository=repository,
        email_client=None,
    )

    with pytest.raises(ValueError, match="agreement_requires_nonzero_quote_total"):
        await service.generate_and_maybe_send(
            AgreementActionRequest(
                customer_id=customer_id,
                thread_id=thread_id,
                quote_id=quote_id,
                client_full_name="Maya Author",
                client_phone="+1 555 123 4567",
                client_email="maya@example.com",
                client_location="Houston, TX",
                effective_date="2026-05-18",
                send_email=False,
            )
        )


@pytest.mark.asyncio
async def test_agreement_action_uses_total_price_range_from_quote(
    document_engine: DocumentEngine,
) -> None:
    repository = InMemoryDocumentRequestRepository()
    thread_id = uuid4()
    customer_id = uuid4()
    quote_id = uuid4()
    repository.quote_records.append(
        SalesPricingQuoteRecord(
            quote_id=quote_id,
            customer_id=customer_id,
            thread_id=thread_id,
            services=["editing_proofreading", "interior_formatting"],
            input_params={},
            used_default_assumptions=False,
            assumptions=None,
            quote_output={
                "total_price_range": {
                    "low": {"amount": "1200.00", "currency": "USD"},
                    "high": {"amount": "1800.00", "currency": "USD"},
                }
            },
            customer_safe_summary="Estimated quote is ready.",
            status="estimated",
        )
    )

    service = AgreementActionService(
        document_engine=document_engine,
        repository=repository,
        email_client=None,
    )

    result = await service.generate_and_maybe_send(
        AgreementActionRequest(
            customer_id=customer_id,
            thread_id=thread_id,
            quote_id=quote_id,
            client_full_name="Maya Author",
            client_phone="+1 555 123 4567",
            client_email="maya@example.com",
            client_location="Houston, TX",
            effective_date="2026-05-18",
            send_email=False,
        )
    )

    assert result.required_params["totalFee"] == "$1,200.00–$1,800.00"
    assert result.required_params["finalFee"] == "$1,200.00–$1,800.00"
