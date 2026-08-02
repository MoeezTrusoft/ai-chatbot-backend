"""Persistence for consultation holiday blackout dates.

CRUD over the ``consultation_holidays`` table plus :func:`refresh_holiday_cache`,
which reloads the in-process cache (see :mod:`holidays`) from the database. The
scheduling engine never touches this repository directly — it reads the cache —
so the only thing that must call in here is the API process (on startup, on the
periodic refresh, and write-through on each mutation).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.consultations.holidays import set_holiday_cache
from bookcraft.components.storage.models import ConsultationHolidayRecord


class HolidayRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def list_all(self) -> list[ConsultationHolidayRecord]:
        statement = select(ConsultationHolidayRecord).order_by(
            col(ConsultationHolidayRecord.holiday_date)
        )
        async with self.session_factory() as session:
            result = await session.execute(statement)
            return list(result.scalars().all())

    async def add(
        self,
        *,
        holiday_date: date,
        label: str | None = None,
        created_by: str | None = None,
    ) -> ConsultationHolidayRecord:
        """Insert (or update the label of) a holiday. Idempotent on the date."""
        async with self.session_factory() as session:
            record = await session.get(ConsultationHolidayRecord, holiday_date)
            if record is None:
                record = ConsultationHolidayRecord(
                    holiday_date=holiday_date,
                    label=label,
                    created_by=created_by,
                )
                session.add(record)
            elif label is not None:
                record.label = label
            await session.commit()
            await session.refresh(record)
            return record

    async def remove(self, *, holiday_date: date) -> bool:
        """Delete a holiday. Returns ``True`` if a row was removed."""
        async with self.session_factory() as session:
            record = await session.get(ConsultationHolidayRecord, holiday_date)
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True


async def refresh_holiday_cache(repository: HolidayRepository) -> set[str]:
    """Reload the in-process holiday cache from the database. Returns the ISO set."""
    records = await repository.list_all()
    dates = {record.holiday_date for record in records}
    set_holiday_cache(dates)
    return {d.isoformat() for d in dates}
