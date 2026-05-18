from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.storage.models import SalesConsultationRecord


class ConsultationRepositoryProtocol(Protocol):
    async def create_appointment(
        self,
        *,
        customer_id: UUID | None,
        lead_id: UUID | None,
        thread_id: UUID,
        customer_name: str,
        customer_email: str | None,
        customer_phone: str | None,
        services: list[str],
        csr_id: str,
        csr_name: str,
        priority_rank: int,
        requested_time_text: str | None,
        customer_timezone: str | None,
        business_timezone: str,
        starts_at_utc: datetime,
        ends_at_utc: datetime,
        houston_display_time: str,
        customer_display_time: str | None,
        duration_minutes: int,
        status: str,
        metadata: dict[str, Any],
    ) -> SalesConsultationRecord: ...

    async def has_conflict(
        self,
        *,
        csr_id: str,
        starts_at_utc: datetime,
        ends_at_utc: datetime,
    ) -> bool: ...


class ConsultationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_appointment(
        self,
        *,
        customer_id: UUID | None,
        lead_id: UUID | None,
        thread_id: UUID,
        customer_name: str,
        customer_email: str | None,
        customer_phone: str | None,
        services: list[str],
        csr_id: str,
        csr_name: str,
        priority_rank: int,
        requested_time_text: str | None,
        customer_timezone: str | None,
        business_timezone: str,
        starts_at_utc: datetime,
        ends_at_utc: datetime,
        houston_display_time: str,
        customer_display_time: str | None,
        duration_minutes: int,
        status: str,
        metadata: dict[str, Any],
    ) -> SalesConsultationRecord:
        record = SalesConsultationRecord(
            customer_id=customer_id,
            lead_id=lead_id,
            thread_id=thread_id,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=customer_phone,
            services=services,
            csr_id=csr_id,
            csr_name=csr_name,
            priority_rank=priority_rank,
            requested_time_text=requested_time_text,
            customer_timezone=customer_timezone,
            business_timezone=business_timezone,
            starts_at_utc=starts_at_utc.replace(tzinfo=None),
            ends_at_utc=ends_at_utc.replace(tzinfo=None),
            houston_display_time=houston_display_time,
            customer_display_time=customer_display_time,
            duration_minutes=duration_minutes,
            status=status,
            metadata_=metadata,
        )

        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def has_conflict(
        self,
        *,
        csr_id: str,
        starts_at_utc: datetime,
        ends_at_utc: datetime,
    ) -> bool:
        start_naive = starts_at_utc.replace(tzinfo=None)
        end_naive = ends_at_utc.replace(tzinfo=None)

        statement = (
            select(SalesConsultationRecord)
            .where(SalesConsultationRecord.csr_id == csr_id)
            .where(SalesConsultationRecord.status == "scheduled")
            .where(col(SalesConsultationRecord.cancelled_at).is_(None))
            .where(col(SalesConsultationRecord.starts_at_utc) < end_naive)
            .where(col(SalesConsultationRecord.ends_at_utc) > start_naive)
        )

        async with self.session_factory() as session:
            result = await session.execute(statement)
            return result.scalars().first() is not None


class InMemoryConsultationRepository:
    def __init__(self) -> None:
        self.records: list[SalesConsultationRecord] = []

    async def create_appointment(
        self,
        *,
        customer_id: UUID | None,
        lead_id: UUID | None,
        thread_id: UUID,
        customer_name: str,
        customer_email: str | None,
        customer_phone: str | None,
        services: list[str],
        csr_id: str,
        csr_name: str,
        priority_rank: int,
        requested_time_text: str | None,
        customer_timezone: str | None,
        business_timezone: str,
        starts_at_utc: datetime,
        ends_at_utc: datetime,
        houston_display_time: str,
        customer_display_time: str | None,
        duration_minutes: int,
        status: str,
        metadata: dict[str, Any],
    ) -> SalesConsultationRecord:
        record = SalesConsultationRecord(
            customer_id=customer_id,
            lead_id=lead_id,
            thread_id=thread_id,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=customer_phone,
            services=services,
            csr_id=csr_id,
            csr_name=csr_name,
            priority_rank=priority_rank,
            requested_time_text=requested_time_text,
            customer_timezone=customer_timezone,
            business_timezone=business_timezone,
            starts_at_utc=starts_at_utc.replace(tzinfo=None),
            ends_at_utc=ends_at_utc.replace(tzinfo=None),
            houston_display_time=houston_display_time,
            customer_display_time=customer_display_time,
            duration_minutes=duration_minutes,
            status=status,
            metadata_=metadata,
        )
        self.records.append(record)
        return record

    async def has_conflict(
        self,
        *,
        csr_id: str,
        starts_at_utc: datetime,
        ends_at_utc: datetime,
    ) -> bool:
        start_naive = starts_at_utc.replace(tzinfo=None)
        end_naive = ends_at_utc.replace(tzinfo=None)

        return any(
            record.csr_id == csr_id
            and record.status == "scheduled"
            and record.cancelled_at is None
            and record.starts_at_utc < end_naive
            and record.ends_at_utc > start_naive
            for record in self.records
        )
