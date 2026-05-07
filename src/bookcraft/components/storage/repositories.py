from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from bookcraft.components.storage.models import ThreadRecord, utc_now


class OptimisticLockConflictError(Exception):
    pass


@dataclass(slots=True)
class ThreadRepository:
    session: AsyncSession

    async def update_state(
        self,
        *,
        thread_id: UUID,
        expected_version: int,
        state: dict[str, Any],
    ) -> ThreadRecord:
        statement = (
            update(ThreadRecord)
            .where(col(ThreadRecord.id) == thread_id)
            .where(col(ThreadRecord.version) == expected_version)
            .values(
                state=state,
                version=ThreadRecord.version + 1,
                updated_at=utc_now(),
            )
            .returning(ThreadRecord)
        )
        result = await self.session.execute(statement)
        updated = result.scalar_one_or_none()
        if updated is None:
            msg = f"Thread {thread_id} version conflict at {expected_version}"
            raise OptimisticLockConflictError(msg)
        return updated
