"""Run only against a disposable PostgreSQL database migrated by CI with Alembic."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bookcraft.components.storage.action_idempotency_repository import ActionIdempotencyRepository
from bookcraft.components.storage.thread_repository import (
    ThreadRepository,
    ThreadVersionConflictError,
)
from bookcraft.domain.state import ThreadState

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="disposable PostgreSQL not configured"
)


async def test_migrated_postgres_ownership_concurrency_and_idempotency():
    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repo = ThreadRepository(factory)
        tid = uuid4()
        await repo.authorize(thread_id=tid, subject="integration-owner", customer_id=None)
        results = await asyncio.gather(
            *[
                repo.save_state(
                    thread_id=tid,
                    state=ThreadState(last_assistant_text=value),
                    expected_version=0,
                    language="en",
                )
                for value in ["one", "two"]
            ],
            return_exceptions=True,
        )
        assert results.count(1) == 1
        assert sum(isinstance(r, ThreadVersionConflictError) for r in results) == 1
        args = dict(
            idempotency_key=str(uuid4()),
            thread_id=tid,
            action_type="create_lead",
            slots_hash="hash",
        )
        claims = await asyncio.gather(
            *[ActionIdempotencyRepository(factory).claim(**args) for _ in range(4)]
        )
        assert claims.count(True) == 1
        order = []

        async def mutation(number):
            async with ThreadRepository(factory).serialize(tid):
                order.append(("start", number))
                await asyncio.sleep(0.01)
                order.append(("end", number))

        await asyncio.gather(mutation(1), mutation(2))
        assert [entry[0] for entry in order] == ["start", "end", "start", "end"]
    finally:
        await engine.dispose()


async def test_advisory_lock_respects_external_holder_and_keeps_query_pool_free():
    import hashlib

    from sqlalchemy import text

    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"], pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = ThreadRepository(factory)
    tid = uuid4()
    key = int.from_bytes(hashlib.sha256(tid.bytes).digest()[:8], "big", signed=True)

    async def lock_and_read():
        async with repo.serialize(tid):
            await repo.load_or_create(thread_id=tid)

    try:
        async with engine.begin() as external:
            await external.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.15):
                    await lock_and_read()
        # With a single query-pool connection, taking the lock must still leave
        # capacity for load_or_create. A shared-pool implementation deadlocks here.
        async with asyncio.timeout(5):
            await lock_and_read()
    finally:
        await engine.dispose()
