from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from bookcraft.components.storage.events import EventChainService
from bookcraft.components.storage.models import Customer, ThreadRecord
from bookcraft.components.storage.repositories import OptimisticLockConflictError, ThreadRepository


@pytest.mark.asyncio
async def test_storage_models_create_and_hash_chain_verifies() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            customer = Customer(email="author@example.com")
            thread = ThreadRecord(customer_id=customer.id)
            session.add(customer)
            session.add(thread)
            events = EventChainService(session)
            await events.append_event(
                thread_id=thread.id,
                event_type="thread.created",
                payload={"source": "test"},
            )
            await events.append_event(
                thread_id=thread.id,
                event_type="state.updated",
                payload={"field": "email"},
            )

        assert await EventChainService(session).verify_chain(thread.id)

    await engine.dispose()


@pytest.mark.asyncio
async def test_thread_repository_detects_optimistic_lock_conflict() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            thread = ThreadRecord(id=uuid4())
            session.add(thread)

        async with session.begin():
            await ThreadRepository(session).update_state(
                thread_id=thread.id,
                expected_version=0,
                state={"schema_version": 1},
            )

        async with session.begin():
            with pytest.raises(OptimisticLockConflictError):
                await ThreadRepository(session).update_state(
                    thread_id=thread.id,
                    expected_version=0,
                    state={"schema_version": 1},
                )

    await engine.dispose()
