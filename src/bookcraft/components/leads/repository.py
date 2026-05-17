from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.leads.schemas import CreateOrUpdateLeadRequest, LeadView
from bookcraft.components.storage.models import SalesLeadRecord, utc_now


@dataclass(slots=True)
class LeadRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def find_by_contact(
        self,
        *,
        email: str | None,
        phone: str | None,
    ) -> SalesLeadRecord | None:
        async with self.session_factory() as session:
            if email:
                result = await session.execute(
                    select(SalesLeadRecord)
                    .where(col(SalesLeadRecord.email) == email)
                    .where(col(SalesLeadRecord.deleted_at).is_(None))
                    .limit(1)
                )
                record = result.scalar_one_or_none()
                if record is not None:
                    return record

            if phone:
                result = await session.execute(
                    select(SalesLeadRecord)
                    .where(col(SalesLeadRecord.phone) == phone)
                    .where(col(SalesLeadRecord.deleted_at).is_(None))
                    .limit(1)
                )
                return result.scalar_one_or_none()

        return None

    async def create(self, request: CreateOrUpdateLeadRequest) -> SalesLeadRecord:
        record = SalesLeadRecord(
            customer_id=request.customer_id,
            thread_id=request.thread_id,
            name=request.name,
            email=request.email,
            phone=request.phone,
            preferred_contact_method=request.preferred_contact_method,
            services=request.services,
            genre=request.genre,
            word_count=request.word_count,
            page_count=request.page_count,
            manuscript_status=request.manuscript_status,
            deadline=request.deadline,
            source=request.source,
            notes=request.notes,
            metadata_=request.metadata,
            created_at=utc_now(),
            updated_at=utc_now(),
        )

        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def update(
        self,
        record: SalesLeadRecord,
        request: CreateOrUpdateLeadRequest,
        *,
        services: list[str],
    ) -> SalesLeadRecord:
        updated = False

        for field_name in [
            "customer_id",
            "thread_id",
            "name",
            "email",
            "phone",
            "preferred_contact_method",
            "genre",
            "word_count",
            "page_count",
            "manuscript_status",
            "deadline",
            "notes",
        ]:
            value = getattr(request, field_name)
            if value is not None and getattr(record, field_name) != value:
                setattr(record, field_name, value)
                updated = True

        if services != record.services:
            record.services = services
            updated = True

        if request.metadata:
            metadata = dict(record.metadata_)
            metadata.update(request.metadata)
            record.metadata_ = metadata
            updated = True

        if updated:
            record.updated_at = utc_now()

        async with self.session_factory() as session:
            merged = await session.merge(record)
            await session.commit()
            await session.refresh(merged)
            return merged

    @staticmethod
    def to_view(record: SalesLeadRecord) -> LeadView:
        return LeadView(
            id=record.id,
            customer_id=record.customer_id,
            thread_id=record.thread_id,
            name=record.name,
            email=record.email,
            phone=record.phone,
            preferred_contact_method=record.preferred_contact_method,
            services=record.services,
            genre=record.genre,
            word_count=record.word_count,
            page_count=record.page_count,
            manuscript_status=record.manuscript_status,
            deadline=record.deadline,
            source=record.source,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class InMemoryLeadRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, SalesLeadRecord] = {}

    async def find_by_contact(
        self,
        *,
        email: str | None,
        phone: str | None,
    ) -> SalesLeadRecord | None:
        if email:
            for record in self.records.values():
                if record.email == email and record.deleted_at is None:
                    return record

        if phone:
            for record in self.records.values():
                if record.phone == phone and record.deleted_at is None:
                    return record

        return None

    async def create(self, request: CreateOrUpdateLeadRequest) -> SalesLeadRecord:
        record = SalesLeadRecord(
            customer_id=request.customer_id,
            thread_id=request.thread_id,
            name=request.name,
            email=request.email,
            phone=request.phone,
            preferred_contact_method=request.preferred_contact_method,
            services=request.services,
            genre=request.genre,
            word_count=request.word_count,
            page_count=request.page_count,
            manuscript_status=request.manuscript_status,
            deadline=request.deadline,
            source=request.source,
            notes=request.notes,
            metadata_=request.metadata,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.records[record.id] = record
        return record

    async def update(
        self,
        record: SalesLeadRecord,
        request: CreateOrUpdateLeadRequest,
        *,
        services: list[str],
    ) -> SalesLeadRecord:
        for field_name in [
            "customer_id",
            "thread_id",
            "name",
            "email",
            "phone",
            "preferred_contact_method",
            "genre",
            "word_count",
            "page_count",
            "manuscript_status",
            "deadline",
            "notes",
        ]:
            value = getattr(request, field_name)
            if value is not None:
                setattr(record, field_name, value)

        record.services = services

        if request.metadata:
            metadata = dict(record.metadata_)
            metadata.update(request.metadata)
            record.metadata_ = metadata

        record.updated_at = utc_now()
        self.records[record.id] = record
        return record

    @staticmethod
    def to_view(record: SalesLeadRecord) -> LeadView:
        return LeadRepository.to_view(record)
