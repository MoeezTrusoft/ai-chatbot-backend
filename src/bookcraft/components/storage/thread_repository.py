from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col, select

from bookcraft.components.storage.events import calculate_event_hash
from bookcraft.components.storage.models import Customer, ThreadEvent, ThreadRecord, utc_now
from bookcraft.components.storage.state_sanitizer import sanitize_thread_state_for_persistence
from bookcraft.domain.state import ThreadState


class ThreadVersionConflictError(RuntimeError):
    pass


class ThreadAccessDeniedError(PermissionError):
    pass


# Weak values avoid retaining one lock per conversation forever.

_local_locks: WeakValueDictionary[tuple[int, UUID], asyncio.Lock] = WeakValueDictionary()
_lock_slots: WeakValueDictionary[int, asyncio.Semaphore] = WeakValueDictionary()


@asynccontextmanager
async def local_thread_lock(thread_id: UUID) -> AsyncIterator[None]:
    key = (id(asyncio.get_running_loop()), thread_id)
    lock = _local_locks.setdefault(key, asyncio.Lock())
    async with lock:
        yield


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

    @asynccontextmanager
    async def serialize(self, thread_id: UUID) -> AsyncIterator[None]:
        """Serialize mutations across PostgreSQL workers, including CSR callbacks.

        SQLite is supported for local tests with a process-local lock. Production
        uses a transaction advisory lock, released on cancellation/connection loss.
        """
        slots = _lock_slots.setdefault(id(asyncio.get_running_loop()), asyncio.Semaphore(4))
        async with local_thread_lock(thread_id), slots:
            async with self.session_factory() as session:
                if session.get_bind().dialect.name != "postgresql":
                    yield
                    return
                lock_id = int.from_bytes(
                    hashlib.sha256(thread_id.bytes).digest()[:8], "big", signed=True
                )
                # Locks must not consume the query pool: otherwise enough waiting
                # turns can hold every connection while their queries wait forever.
                lock_engine = create_async_engine(session.get_bind().engine.url, poolclass=NullPool)
            try:
                async with lock_engine.begin() as connection:
                    await connection.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id}
                    )
                    yield
            finally:
                await lock_engine.dispose()

    async def authorize(self, *, thread_id: UUID, subject: str, customer_id: UUID | None) -> None:
        """Claim a new thread, or verify an existing thread's authenticated owner."""
        async with self.serialize(thread_id):
            async with self.session_factory() as session:
                record = await session.get(ThreadRecord, thread_id)
                if record is not None:
                    owned = record.owner_subject == subject and (
                        customer_id is None or record.customer_id == customer_id
                    )
                    legacy_owned = (
                        record.owner_subject is None
                        and customer_id is not None
                        and record.customer_id == customer_id
                    )
                    if record.deleted_at is not None or not (owned or legacy_owned):
                        raise ThreadAccessDeniedError("Thread is not accessible")
                    return
            await self.load_or_create(
                thread_id=thread_id, customer_id=customer_id, owner_subject=subject
            )

    async def load_or_create(
        self,
        *,
        thread_id: UUID | None,
        customer_id: UUID | None = None,
        owner_subject: str | None = None,
    ) -> LoadedThread:
        resolved_thread_id = thread_id or uuid4()

        async with self.session_factory() as session:
            record = await session.get(ThreadRecord, resolved_thread_id)

            if record is None:
                if customer_id is not None and await session.get(Customer, customer_id) is None:
                    session.add(
                        Customer(
                            id=customer_id,
                            metadata_={"source": "chat_session"},
                        )
                    )

                record = ThreadRecord(
                    id=resolved_thread_id,
                    customer_id=customer_id,
                    owner_subject=owner_subject,
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
                if record.deleted_at is not None:
                    raise ThreadAccessDeniedError("Thread is not accessible")
                if customer_id is not None and record.customer_id != customer_id:
                    raise ThreadAccessDeniedError("Thread is not accessible")
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

    async def append_events_batch(
        self,
        *,
        thread_id: UUID,
        events: list[dict[str, Any]],
    ) -> str:
        """Insert multiple events in a single DB transaction. Returns last event hash."""
        async with self.session_factory() as session:
            for evt in events:
                session.add(
                    ThreadEvent(
                        thread_id=thread_id,
                        sequence=evt["sequence"],
                        event_type=evt["event_type"],
                        actor=evt.get("actor", "system"),
                        payload=evt["payload"],
                        previous_hash=evt["previous_hash"],
                        event_hash=evt["event_hash"],
                        created_at=utc_now(),
                    )
                )
            await session.commit()
        return str(events[-1]["event_hash"])

    async def save_state(
        self,
        *,
        thread_id: UUID,
        state: ThreadState,
        expected_version: int,
        language: str,
        pending_events: list[dict[str, Any]] | None = None,
    ) -> int:
        async with self.session_factory() as session:
            statement = (
                update(ThreadRecord)
                .where(col(ThreadRecord.id) == thread_id)
                .where(col(ThreadRecord.version) == expected_version)
                .where(col(ThreadRecord.deleted_at).is_(None))
                .values(
                    state=sanitize_thread_state_for_persistence(state),
                    version=expected_version + 1,
                    turn_count=col(ThreadRecord.turn_count) + 1,
                    language=language,
                    updated_at=utc_now(),
                    last_message_at=utc_now(),
                )
                .returning(col(ThreadRecord.version))
            )
            version = (await session.execute(statement)).scalar_one_or_none()
            if version is None:
                raise ThreadVersionConflictError(f"Thread {thread_id} changed during the turn")
            for event in pending_events or []:
                session.add(ThreadEvent(thread_id=thread_id, **event))
            await session.commit()
            if pending_events is not None:
                pending_events.clear()
            return int(version)

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
