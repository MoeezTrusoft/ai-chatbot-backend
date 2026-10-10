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


async def test_sales_receipt_recovery_and_concurrent_replay_on_migrated_database(tmp_path):
    import httpx

    from bookcraft.components.storage.management_models import WorkflowRecord
    from bookcraft.infra.config import Settings
    from bookcraft.infra.sales_mcp import SalesError, SalesMcpClient
    from bookcraft.services.sales import SalesService
    from tests.integration.test_sales_alignment import CUSTOMER, TENANT, SalesFixture, service_token
    from tests.unit.test_api_auth_unit import SIGNING_KEY

    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = ThreadRepository(sessions)
    thread, operation = uuid4(), uuid4()
    token_file = tmp_path / "sales.jwt"
    await asyncio.to_thread(token_file.write_text, service_token(), encoding="utf-8")
    settings = Settings(
        app_env="test",
        api_auth_mode="jwt",
        jwt_signing_key=SIGNING_KEY,
        sales_integration_mode="erp_mcp",
        sales_mcp_url="https://sales.example.invalid/mcp",
        sales_service_jwt_file=str(token_file),
        sales_tenant_id=TENANT,
        sales_actor_id="chatbot",
        sales_mcp_internal_secret="internal-test-secret",  # noqa: S106 - synthetic credential
    )
    from uuid import UUID

    await repository.authorize(thread_id=thread, subject="bridge", customer_id=UUID(CUSTOMER))
    fixture = SalesFixture()
    client = SalesMcpClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(fixture)))
    service = SalesService(client, sessions)
    arguments = {"startTime": "2030-10-12T22:15:00+05:00", "userTimezone": "Asia/Karachi"}

    async def book():
        async with repository.serialize(thread):
            return await service.mutate(thread, operation, "schedule", arguments)

    try:
        fixture.lose_response = True
        with pytest.raises(SalesError, match="sales_outcome_unknown"):
            await book()
        async with sessions() as session:
            assert (await session.get(WorkflowRecord, operation)).status == "pending"
        # New service instance, same persisted database, simultaneous callers.
        service = SalesService(client, sessions)
        results = await asyncio.gather(book(), book())
        assert results[0]["id"] == results[1]["id"]
        assert len(fixture.rows) == 1
        assert len([name for name, _ in fixture.calls if name.endswith("_schedule")]) == 2
        async with sessions() as session:
            assert (await session.get(WorkflowRecord, operation)).status == "acknowledged"
    finally:
        await client.aclose()
        await engine.dispose()


async def test_database_exclusion_blocks_overlap_and_releases_cancelled_slot():
    from datetime import datetime, timedelta

    from bookcraft.components.consultations.repository import (
        AppointmentConflictError,
        commit_appointment,
    )
    from bookcraft.components.storage.models import SalesConsultationRecord

    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    csr, start = str(uuid4()), datetime(2030, 1, 7, 16)

    async def insert(at):
        async with factory() as session:
            row = SalesConsultationRecord(
                customer_name="Synthetic",
                requested_time_text=at.isoformat(),
                csr_id=csr,
                csr_name="Test",
                starts_at_utc=at,
                ends_at_utc=at + timedelta(minutes=30),
                houston_display_time="Test",
            )
            session.add(row)
            await commit_appointment(session)
            return row.id

    try:
        results = await asyncio.gather(
            insert(start), insert(start + timedelta(minutes=15)), return_exceptions=True
        )
        assert sum(isinstance(row, AppointmentConflictError) for row in results) == 1, [
            repr(row) for row in results
        ]
        winner = next(row for row in results if not isinstance(row, Exception))
        async with factory() as session:
            row = await session.get(SalesConsultationRecord, winner)
            row.status = "cancelled"
            session.add(row)
            await session.commit()
        await insert(start)
        await insert(start + timedelta(minutes=30))
        with pytest.raises(AppointmentConflictError):
            await insert(start + timedelta(minutes=1))
    finally:
        await engine.dispose()


async def test_migrated_booking_api_rejects_race_without_partial_state():
    import httpx
    from fastapi import FastAPI
    from sqlmodel import select

    from bookcraft.api.appointments import router
    from bookcraft.components.consultations.holidays import set_holiday_cache
    from bookcraft.components.storage.models import DeliveryOutboxRecord, ThreadRecord
    from bookcraft.infra.config import Settings
    from tests.integration.test_management_features import booking_payload

    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.state.settings = Settings(app_env="test", api_auth_mode="off", csr_node_api_url="")
    app.state.session_factory = factory
    app.include_router(router)
    tids = [uuid4(), uuid4()]
    async with factory() as session:
        session.add_all([ThreadRecord(id=tid) for tid in tids])
        await session.commit()
    set_holiday_cache([])
    payload = booking_payload()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.post(
                        f"/api/v1/customer/threads/{tid}/appointments",
                        json={**payload, "appointment_id": str(uuid4())},
                    )
                    for tid in tids
                ]
            )
            assert sorted(r.status_code for r in responses) == [200, 409], [
                r.text for r in responses
            ]
            loser = tids[
                next(i for i, response in enumerate(responses) if response.status_code == 409)
            ]
            async with factory() as session:
                assert (await session.get(ThreadRecord, loser)).version == 0
                rows = (await session.execute(select(DeliveryOutboxRecord))).scalars().all()
                assert not any(row.payload.get("threadId") == str(loser) for row in rows)
    finally:
        await engine.dispose()


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
