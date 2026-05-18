from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.storage.models import (
    SalesDocumentRequestRecord,
    SalesPricingQuoteRecord,
    utc_now,
)


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

    async def latest_quote_for_agreement(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        quote_id: UUID | None = None,
    ) -> SalesPricingQuoteRecord | None:
        async with self.session_factory() as session:
            statement = select(SalesPricingQuoteRecord)

            if quote_id is not None:
                statement = statement.where(SalesPricingQuoteRecord.quote_id == quote_id)
            else:
                statement = statement.where(SalesPricingQuoteRecord.thread_id == thread_id)
                if customer_id is not None:
                    statement = statement.where(SalesPricingQuoteRecord.customer_id == customer_id)

            statement = statement.order_by(col(SalesPricingQuoteRecord.created_at).desc())
            result = await session.execute(statement)
            return result.scalars().first()


class InMemoryDocumentRequestRepository:
    def __init__(self) -> None:
        self.records: list[SalesDocumentRequestRecord] = []
        self.quote_records: list[SalesPricingQuoteRecord] = []

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

    async def latest_quote_for_agreement(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        quote_id: UUID | None = None,
    ) -> SalesPricingQuoteRecord | None:
        candidates = self.quote_records
        if quote_id is not None:
            candidates = [record for record in candidates if record.quote_id == quote_id]
        else:
            candidates = [record for record in candidates if record.thread_id == thread_id]
            if customer_id is not None:
                candidates = [record for record in candidates if record.customer_id == customer_id]

        if not candidates:
            return None

        return sorted(candidates, key=lambda record: record.created_at, reverse=True)[0]
