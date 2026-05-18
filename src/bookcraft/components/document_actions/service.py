from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from bookcraft.components.document_actions.schemas import NDAActionRequest, NDAActionResult
from bookcraft.components.documents.engine import DocumentEngine
from bookcraft.components.documents.schemas import DocumentStatus, NDAParams
from bookcraft.components.storage.models import SalesDocumentRequestRecord
from bookcraft.infra.email import EmailAttachment, SMTPEmailClient


class DocumentRequestRepositoryProtocol(Protocol):
    async def create_request(
        self,
        *,
        customer_id: UUID | None,
        lead_id: UUID | None,
        thread_id: UUID,
        document_type: str,
        quote_id: UUID | None,
        required_params: dict[str, Any],
        status: str,
        document_id: str | None,
        recipient_email: str | None,
        delivery_status: str | None,
        provider_message_id: str | None,
        html_path: str | None,
        pdf_path: str | None,
        error_code: str | None,
        sent: bool,
    ) -> SalesDocumentRequestRecord: ...


@dataclass(slots=True)
class NDAActionService:
    document_engine: DocumentEngine
    repository: DocumentRequestRepositoryProtocol
    email_client: SMTPEmailClient | None = None

    async def generate_and_maybe_send(self, request: NDAActionRequest) -> NDAActionResult:
        params = NDAParams(
            date=request.effective_date,
            authorTitle=request.author_title,
            authorFullName=request.author_full_name,
            authorPhone=request.author_phone,
            authorEmail=request.author_email,
            signature=request.signature or request.author_full_name,
        )
        document = self.document_engine.generate_nda(params)

        delivery_status: str | None = None
        provider_message_id: str | None = None
        error_code: str | None = None

        if request.send_email:
            if self.email_client is None:
                delivery_status = "failed"
                error_code = "email_client_unavailable"
            else:
                attachments = []
                if document.pdf_path:
                    attachments.append(
                        EmailAttachment(
                            path=Path(document.pdf_path),
                            filename=f"{document.document_id}.pdf",
                            content_type="application/pdf",
                        )
                    )
                elif document.html_path:
                    attachments.append(
                        EmailAttachment(
                            path=Path(document.html_path),
                            filename=f"{document.document_id}.html",
                            content_type="text/html",
                        )
                    )

                email_result = self.email_client.send(
                    to_email=request.author_email,
                    subject="Your BookCraft NDA",
                    text_body=(
                        "Hi,\\n\\nAttached is the NDA prepared for your BookCraft project.\\n\\n"
                        "Best,\\nBookCraft Publishers"
                    ),
                    html_body=(
                        "<p>Hi,</p><p>Attached is the NDA prepared for your BookCraft "
                        "project.</p><p>Best,<br>BookCraft Publishers</p>"
                    ),
                    attachments=attachments,
                )
                delivery_status = "sent" if email_result.success else "failed"
                provider_message_id = email_result.provider_message_id
                error_code = email_result.error_code

        status = (
            "sent"
            if delivery_status == "sent"
            else document.status.value
            if document.status == DocumentStatus.VERIFIED
            else document.status.value
        )

        html_path = str(document.html_path) if document.html_path else None
        pdf_path = str(document.pdf_path) if document.pdf_path else None

        record = await self.repository.create_request(
            customer_id=request.customer_id,
            lead_id=request.lead_id,
            thread_id=request.thread_id,
            document_type="nda",
            quote_id=None,
            required_params=request.model_dump(mode="json"),
            status=status,
            document_id=document.document_id,
            recipient_email=request.author_email,
            delivery_status=delivery_status,
            provider_message_id=provider_message_id,
            html_path=html_path,
            pdf_path=pdf_path,
            error_code=error_code,
            sent=delivery_status == "sent",
        )

        return NDAActionResult(
            request_id=record.id,
            document_id=document.document_id,
            status=status,
            delivery_status=delivery_status,
            recipient_email=request.author_email,
            html_path=html_path,
            pdf_path=pdf_path,
            provider_message_id=provider_message_id,
            error_code=error_code,
            required_params=request.model_dump(mode="json"),
            customer_safe_summary=_customer_safe_summary(
                status=status,
                delivery_status=delivery_status,
                email=request.author_email,
                error_code=error_code,
            ),
        )


def _customer_safe_summary(
    *,
    status: str,
    delivery_status: str | None,
    email: str,
    error_code: str | None,
) -> str:
    if delivery_status == "sent":
        return f"I prepared the NDA and sent it to {email}."

    if delivery_status == "failed":
        return (
            "I prepared the NDA, but the email could not be sent right now. "
            f"Delivery status: {error_code or 'failed'}."
        )

    if status in {"verified", "generated"}:
        return f"I prepared the NDA for {email}; it is ready to send once confirmed."

    return "I tried to prepare the NDA, but it needs review before sending."
