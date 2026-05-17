from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from bookcraft.components.storage.models import PortfolioSampleViewRecord, utc_now


@dataclass(slots=True)
class PortfolioViewRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def list_seen_sample_ids(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        service_category: str | None = None,
    ) -> list[str]:
        async with self.session_factory() as session:
            statement = select(PortfolioSampleViewRecord).where(
                PortfolioSampleViewRecord.thread_id == thread_id
            )
            if customer_id is not None:
                statement = statement.where(PortfolioSampleViewRecord.customer_id == customer_id)
            if service_category:
                statement = statement.where(
                    PortfolioSampleViewRecord.service_category == service_category
                )

            result = await session.execute(statement)
            records = result.scalars().all()

        ordered: list[str] = []
        for record in records:
            if record.sample_id not in ordered:
                ordered.append(record.sample_id)
        return ordered

    async def save_views(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        service_category: str,
        genre: str | None,
        sample_ids: list[str],
    ) -> None:
        if not sample_ids:
            return

        async with self.session_factory() as session:
            for sample_id in sample_ids:
                session.add(
                    PortfolioSampleViewRecord(
                        customer_id=customer_id,
                        thread_id=thread_id,
                        sample_id=sample_id,
                        service_category=service_category,
                        genre=genre,
                        shown_at=utc_now(),
                    )
                )
            await session.commit()


class InMemoryPortfolioViewRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def list_seen_sample_ids(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        service_category: str | None = None,
    ) -> list[str]:
        ordered: list[str] = []
        for record in self.records:
            if record["thread_id"] != thread_id:
                continue
            if customer_id is not None and record["customer_id"] != customer_id:
                continue
            if service_category and record["service_category"] != service_category:
                continue
            sample_id = str(record["sample_id"])
            if sample_id not in ordered:
                ordered.append(sample_id)
        return ordered

    async def save_views(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        service_category: str,
        genre: str | None,
        sample_ids: list[str],
    ) -> None:
        for sample_id in sample_ids:
            self.records.append(
                {
                    "customer_id": customer_id,
                    "thread_id": thread_id,
                    "sample_id": sample_id,
                    "service_category": service_category,
                    "genre": genre,
                }
            )
