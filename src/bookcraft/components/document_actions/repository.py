from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bookcraft.components.storage.models import SalesDocumentRequestRecord, utc_now


@dataclass(slots=True)
class DocumentRequestRepository:
    session_factory: async_sessionmaker[AsyncSession]

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
    ) -> SalesDocumentRequestRecord:
        now = utc_now()
        record = SalesDocumentRequestRecord(
            customer_id=customer_id,
            lead_id=lead_id,
            thread_id=thread_id,
            document_type=document_type,
            quote_id=quote_id,
            required_params=required_params,
            status=status,
            document_id=document_id,
            recipient_email=recipient_email,
            delivery_status=delivery_status,
            provider_message_id=provider_message_id,
            html_path=html_path,
            pdf_path=pdf_path,
            error_code=error_code,
            created_at=now,
            updated_at=now,
            sent_at=now if sent else None,
        )

        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record


class InMemoryDocumentRequestRepository:
    def __init__(self) -> None:
        self.records: list[SalesDocumentRequestRecord] = []

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
    ) -> SalesDocumentRequestRecord:
        now = utc_now()
        record = SalesDocumentRequestRecord(
            customer_id=customer_id,
            lead_id=lead_id,
            thread_id=thread_id,
            document_type=document_type,
            quote_id=quote_id,
            required_params=required_params,
            status=status,
            document_id=document_id,
            recipient_email=recipient_email,
            delivery_status=delivery_status,
            provider_message_id=provider_message_id,
            html_path=html_path,
            pdf_path=pdf_path,
            error_code=error_code,
            created_at=now,
            updated_at=now,
            sent_at=now if sent else None,
        )
        self.records.append(record)
        return record
