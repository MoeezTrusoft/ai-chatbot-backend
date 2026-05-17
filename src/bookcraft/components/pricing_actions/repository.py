from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bookcraft.components.storage.models import SalesPricingQuoteRecord, utc_now


@dataclass(slots=True)
class PricingQuoteRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def save_quote(
        self,
        *,
        quote_id: UUID,
        lead_id: UUID | None,
        customer_id: UUID | None,
        thread_id: UUID,
        services: list[str],
        input_params: dict[str, Any],
        used_default_assumptions: bool,
        assumptions: dict[str, Any] | None,
        quote_output: dict[str, Any],
        customer_safe_summary: str,
        status: str,
    ) -> SalesPricingQuoteRecord:
        record = SalesPricingQuoteRecord(
            quote_id=quote_id,
            lead_id=lead_id,
            customer_id=customer_id,
            thread_id=thread_id,
            services=services,
            input_params=input_params,
            used_default_assumptions=used_default_assumptions,
            assumptions=assumptions,
            quote_output=quote_output,
            customer_safe_summary=customer_safe_summary,
            status=status,
            created_at=utc_now(),
            updated_at=utc_now(),
        )

        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record


class InMemoryPricingQuoteRepository:
    def __init__(self) -> None:
        self.records: list[SalesPricingQuoteRecord] = []

    async def save_quote(
        self,
        *,
        quote_id: UUID,
        lead_id: UUID | None,
        customer_id: UUID | None,
        thread_id: UUID,
        services: list[str],
        input_params: dict[str, Any],
        used_default_assumptions: bool,
        assumptions: dict[str, Any] | None,
        quote_output: dict[str, Any],
        customer_safe_summary: str,
        status: str,
    ) -> SalesPricingQuoteRecord:
        record = SalesPricingQuoteRecord(
            quote_id=quote_id,
            lead_id=lead_id,
            customer_id=customer_id,
            thread_id=thread_id,
            services=services,
            input_params=input_params,
            used_default_assumptions=used_default_assumptions,
            assumptions=assumptions,
            quote_output=quote_output,
            customer_safe_summary=customer_safe_summary,
            status=status,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.records.append(record)
        return record
