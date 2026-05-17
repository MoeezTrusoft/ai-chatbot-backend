from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from bookcraft.components.document_actions import NDAActionRequest, NDAActionService
from bookcraft.components.document_actions.repository import (
    InMemoryDocumentRequestRepository,
)
from bookcraft.components.documents.engine import DocumentEngine
from bookcraft.components.documents.registry import DocumentTemplateRegistry
from bookcraft.infra.email import EmailSendResult


class FakeEmailClient:
    def __init__(self, *, success: bool = True) -> None:
        self.success = success
        self.sent_to: str | None = None

    def send(self, **kwargs: object) -> EmailSendResult:
        self.sent_to = str(kwargs["to_email"])
        return EmailSendResult(
            success=self.success,
            provider_message_id="fake-message-id" if self.success else None,
            error_code=None if self.success else "fake_failed",
        )


@pytest.fixture()
def document_engine(tmp_path: Path) -> DocumentEngine:
    return DocumentEngine(
        registry=DocumentTemplateRegistry("data/templates"),
        output_dir=tmp_path,
        pdf_rendering_enabled=False,
    )


@pytest.mark.asyncio
async def test_nda_action_generates_without_email(
    document_engine: DocumentEngine,
) -> None:
    repository = InMemoryDocumentRequestRepository()
    service = NDAActionService(
        document_engine=document_engine,
        repository=repository,
        email_client=None,
    )

    result = await service.generate_and_maybe_send(
        NDAActionRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            author_full_name="Maya Author",
            author_phone="+1 555 123 4567",
            author_email="maya@example.com",
            effective_date="2026-05-18",
            send_email=False,
        )
    )

    assert result.document_id is not None
    assert result.status in {"verified", "generated"}
    assert result.delivery_status is None
    assert repository.records


@pytest.mark.asyncio
async def test_nda_action_sends_email_after_confirmation(
    document_engine: DocumentEngine,
) -> None:
    repository = InMemoryDocumentRequestRepository()
    email_client = FakeEmailClient(success=True)
    service = NDAActionService(
        document_engine=document_engine,
        repository=repository,
        email_client=email_client,  # type: ignore[arg-type]
    )

    result = await service.generate_and_maybe_send(
        NDAActionRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            author_full_name="Maya Author",
            author_phone="+1 555 123 4567",
            author_email="maya@example.com",
            effective_date="2026-05-18",
            send_email=True,
        )
    )

    assert result.delivery_status == "sent"
    assert result.provider_message_id == "fake-message-id"
    assert email_client.sent_to == "maya@example.com"
    assert repository.records[0].sent_at is not None
