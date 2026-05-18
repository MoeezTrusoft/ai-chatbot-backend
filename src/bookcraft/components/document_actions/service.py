from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from bookcraft.components.document_actions.schemas import (
    AgreementActionRequest,
    AgreementActionResult,
    NDAActionRequest,
    NDAActionResult,
)
from bookcraft.components.documents.engine import DocumentEngine
from bookcraft.components.documents.schemas import (
    AgreementParams,
    DocumentStatus,
    NDAParams,
    SelectedService,
    ServiceItem,
)
from bookcraft.components.storage.models import (
    SalesDocumentRequestRecord,
    SalesPricingQuoteRecord,
)
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

    async def latest_quote_for_agreement(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        quote_id: UUID | None = None,
    ) -> SalesPricingQuoteRecord | None: ...


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


@dataclass(slots=True)
class AgreementActionService:
    document_engine: DocumentEngine
    repository: DocumentRequestRepositoryProtocol
    email_client: SMTPEmailClient | None = None

    async def generate_and_maybe_send(
        self,
        request: AgreementActionRequest,
    ) -> AgreementActionResult:
        quote_record = await self.repository.latest_quote_for_agreement(
            customer_id=request.customer_id,
            thread_id=request.thread_id,
            quote_id=request.quote_id,
        )
        if quote_record is None:
            raise ValueError("agreement_requires_existing_quote")

        params = _agreement_params_from_quote(request=request, quote_record=quote_record)
        if _is_zero_money(str(params.model_dump(by_alias=True).get("totalFee", "$0.00"))):
            raise ValueError("agreement_requires_nonzero_quote_total")

        document = self.document_engine.generate_agreement(params)

        html_path = str(document.html_path) if document.html_path else None
        pdf_path = str(document.pdf_path) if document.pdf_path else None

        delivery_status: str | None = None
        provider_message_id: str | None = None
        error_code: str | None = None

        if request.send_email:
            if self.email_client is None:
                delivery_status = "failed"
                error_code = "email_client_unavailable"
            else:
                attachments = []
                if pdf_path:
                    attachments.append(
                        EmailAttachment(
                            path=Path(pdf_path),
                            filename=f"{document.document_id}.pdf",
                            content_type="application/pdf",
                        )
                    )
                elif html_path:
                    attachments.append(
                        EmailAttachment(
                            path=Path(html_path),
                            filename=f"{document.document_id}.html",
                            content_type="text/html",
                        )
                    )

                email_result = self.email_client.send(
                    to_email=request.client_email,
                    subject="Your BookCraft Service Agreement",
                    text_body=(
                        "Hi,\\n\\nAttached is the service agreement prepared for "
                        "your BookCraft project.\\n\\nBest,\\nBookCraft Publishers"
                    ),
                    html_body=(
                        "<p>Hi,</p><p>Attached is the service agreement prepared "
                        "for your BookCraft project.</p>"
                        "<p>Best,<br>BookCraft Publishers</p>"
                    ),
                    attachments=attachments,
                )
                delivery_status = "sent" if email_result.success else "failed"
                provider_message_id = email_result.provider_message_id
                error_code = email_result.error_code

        status = "sent" if delivery_status == "sent" else document.status.value

        record = await self.repository.create_request(
            customer_id=request.customer_id,
            lead_id=request.lead_id,
            thread_id=request.thread_id,
            document_type="agreement",
            quote_id=quote_record.quote_id,
            required_params=params.model_dump(by_alias=True, mode="json"),
            status=status,
            document_id=document.document_id,
            recipient_email=request.client_email,
            delivery_status=delivery_status,
            provider_message_id=provider_message_id,
            html_path=html_path,
            pdf_path=pdf_path,
            error_code=error_code,
            sent=delivery_status == "sent",
        )

        return AgreementActionResult(
            request_id=record.id,
            document_id=document.document_id,
            quote_id=quote_record.quote_id,
            status=status,
            delivery_status=delivery_status,
            recipient_email=request.client_email,
            html_path=html_path,
            pdf_path=pdf_path,
            provider_message_id=provider_message_id,
            error_code=error_code,
            required_params=params.model_dump(by_alias=True, mode="json"),
            customer_safe_summary=_agreement_customer_safe_summary(
                status=status,
                delivery_status=delivery_status,
                email=request.client_email,
                error_code=error_code,
            ),
        )


def _agreement_params_from_quote(
    *,
    request: AgreementActionRequest,
    quote_record: SalesPricingQuoteRecord,
) -> AgreementParams:
    quote_output = quote_record.quote_output or {}
    total_fee = _money_from_quote_output(quote_output)
    services = _services_from_quote_record(quote_record)

    return AgreementParams(
        logoPath="",
        effectiveDate=request.effective_date,
        abbreviation="BCP",
        clientFullName=request.client_full_name,
        clientPhone=request.client_phone,
        clientEmail=request.client_email,
        clientLocation=request.client_location,
        filteredServices=services,
        finalFee=total_fee,
        totalFee=total_fee,
        discountPercent=0,
        scheduleType="full_payment",
        initialPercentage=0,
        remainingPercentage=0,
        numberOfMonths=0,
        installmentAmount="0",
        initialAmount="0",
        remainingAmount="0",
        advancePercentage=0,
        finalPercentage=0,
        beforeOrAfter=True,
        finalMilestoneService=services[-1].title if services else "Project delivery",
        milestones=[],
        signature=request.signature or request.client_full_name,
        agreementDate=request.effective_date,
    )


def _money_from_quote_output(quote_output: dict[str, Any]) -> str:
    range_value = quote_output.get("total_price_range") or quote_output.get("subtotal_range")
    if isinstance(range_value, dict):
        low = _amount_from_money_like(range_value.get("low"))
        high = _amount_from_money_like(range_value.get("high"))

        if low and high:
            if low == high:
                return _format_money(low)
            return f"{_format_money(low)}–{_format_money(high)}"
        if low:
            return _format_money(low)
        if high:
            return _format_money(high)

    candidate_keys = (
        "final_fee",
        "total_fee",
        "estimated_total",
        "total",
        "price",
        "amount",
    )
    for key in candidate_keys:
        amount = _amount_from_money_like(quote_output.get(key))
        if amount:
            return _format_money(amount)

    for value in quote_output.values():
        if isinstance(value, dict):
            nested = _money_from_quote_output(value)
            if not _is_zero_money(nested):
                return nested

    return "$0.00"


def _amount_from_money_like(value: object) -> float | None:
    if isinstance(value, dict):
        return _amount_from_money_like(value.get("amount"))

    if isinstance(value, (int, float)) and value > 0:
        return float(value)

    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            amount = float(cleaned)
        except ValueError:
            return None
        return amount if amount > 0 else None

    return None


def _format_money(amount: float) -> str:
    return f"${amount:,.2f}"


def _is_zero_money(value: str) -> bool:
    cleaned = value.replace("$", "").replace(",", "").strip()
    if "–" in cleaned:
        parts = [part.strip() for part in cleaned.split("–")]
        return all(_is_zero_money(part) for part in parts)

    try:
        return float(cleaned) <= 0
    except ValueError:
        return False


def _services_from_quote_record(
    quote_record: SalesPricingQuoteRecord,
) -> list[SelectedService]:
    services = quote_record.services or ["book_publishing_services"]
    return [
        SelectedService(
            title=_humanize_service_name(service),
            items=[
                ServiceItem(
                    title="Quoted scope",
                    description=(
                        "Service scope and delivery terms are based on the approved "
                        f"BookCraft quote {quote_record.quote_id}."
                    ),
                )
            ],
        )
        for service in services
    ]


def _humanize_service_name(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def _agreement_customer_safe_summary(
    *,
    status: str,
    delivery_status: str | None,
    email: str,
    error_code: str | None,
) -> str:
    if delivery_status == "sent":
        return f"I prepared the service agreement and sent it to {email}."

    if delivery_status == "failed":
        return (
            "I prepared the service agreement, but the email could not be sent "
            f"right now. Delivery status: {error_code or 'failed'}."
        )

    if status in {"verified", "generated"}:
        return f"I prepared the service agreement for {email}; it is ready to send once confirmed."

    return "I tried to prepare the service agreement, but it needs review before sending."
