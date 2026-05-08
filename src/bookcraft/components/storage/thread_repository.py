from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.storage.events import calculate_event_hash
from bookcraft.components.storage.models import ThreadEvent, ThreadRecord, utc_now
from bookcraft.domain.state import ThreadState


class ThreadVersionConflictError(RuntimeError):
    pass


@dataclass(slots=True)
class LoadedThread:
    thread_id: UUID
    state: ThreadState
    version: int
    turn_count: int
    event_count: int
    last_event_hash: str | None


@dataclass(slots=True)
class ThreadRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def load_or_create(
        self,
        *,
        thread_id: UUID | None,
        customer_id: UUID | None = None,
    ) -> LoadedThread:
        resolved_thread_id = thread_id or uuid4()

        async with self.session_factory() as session:
            record = await session.get(ThreadRecord, resolved_thread_id)

            if record is None:
                record = ThreadRecord(
                    id=resolved_thread_id,
                    customer_id=customer_id,
                    state=ThreadState().model_dump(mode="json"),
                    version=0,
                    turn_count=0,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(record)
                await session.commit()
                event_count = 0
                last_hash = None
            else:
                event_count, last_hash = await self._event_tail(session, resolved_thread_id)

            return LoadedThread(
                thread_id=resolved_thread_id,
                state=ThreadState.model_validate(record.state),
                version=record.version,
                turn_count=record.turn_count,
                event_count=event_count,
                last_event_hash=last_hash,
            )

    async def append_event(
        self,
        *,
        thread_id: UUID,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        previous_hash: str | None,
        actor: str = "system",
    ) -> str:
        event_hash = calculate_event_hash(
            thread_id=thread_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            previous_hash=previous_hash,
        )

        async with self.session_factory() as session:
            session.add(
                ThreadEvent(
                    thread_id=thread_id,
                    sequence=sequence,
                    event_type=event_type,
                    actor=actor,
                    payload=payload,
                    previous_hash=previous_hash,
                    event_hash=event_hash,
                    created_at=utc_now(),
                )
            )
            await session.commit()

        return event_hash

    async def save_state(
        self,
        *,
        thread_id: UUID,
        state: ThreadState,
        expected_version: int,
        language: str,
    ) -> int:
        async with self.session_factory() as session:
            record = await session.get(ThreadRecord, thread_id)
            if record is None:
                raise KeyError(f"Thread not found: {thread_id}")

            if record.version != expected_version:
                raise ThreadVersionConflictError(
                    f"Thread {thread_id} version conflict: "
                    f"expected {expected_version}, found {record.version}"
                )

            record.state = state.model_dump(mode="json")
            record.version += 1
            record.turn_count += 1
            record.language = language
            record.updated_at = utc_now()
            record.last_message_at = utc_now()

            session.add(record)
            await session.commit()
            await session.refresh(record)

            return record.version

    async def _event_tail(
        self,
        session: AsyncSession,
        thread_id: UUID,
    ) -> tuple[int, str | None]:
        result = await session.execute(
            select(ThreadEvent)
            .where(col(ThreadEvent.thread_id) == thread_id)
            .order_by(col(ThreadEvent.sequence).desc())
            .limit(1)
        )
        last_event = result.scalar_one_or_none()

        if last_event is None:
            return 0, None
        return last_event.sequence, last_event.event_hash
