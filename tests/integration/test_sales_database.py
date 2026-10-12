"""Opt-in real Sales/PostgreSQL acceptance; never connects to a production database."""

import asyncio
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import httpx
import httpx2 as mcp_http
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from bookcraft.api.main import create_app
from bookcraft.components.storage.models import DeliveryOutboxRecord, SalesConsultationRecord
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import SalesMcpClient
from bookcraft.services.sales import SalesService

SERVER_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ops/sales_database_server.mjs"


class LoseCommittedResponse(mcp_http.AsyncBaseTransport):
    """Discard one real successful schedule response, after Sales commits it."""

    def __init__(self):
        self.pool = mcp_http.AsyncHTTPTransport(retries=0)
        self.armed = False

    async def handle_async_request(self, request):
        response = await self.pool.handle_async_request(request)
        body = json.loads(request.content) if request.method == "POST" else {}
        if self.armed and body.get("params", {}).get("name") == "sales_consultation_schedule":
            await response.aread()
            await response.aclose()
            self.armed = False
            raise mcp_http.ReadTimeout("Synthetic lost response after real commit", request=request)
        return response

    async def aclose(self):
        await self.pool.aclose()


@pytest.fixture
async def real_sales(tmp_path):
    checkout = os.environ.get("SALES_TEST_CHECKOUT")
    if not checkout or not os.environ.get("SALES_TEST_ADMIN_URL"):
        pytest.skip("Set SALES_TEST_CHECKOUT and loopback SALES_TEST_ADMIN_URL")
    node = shutil.which("node")
    assert node, "Node.js is required"
    checkout_path = await asyncio.to_thread(Path(checkout).resolve)
    with (tmp_path / "sales.log").open("w", encoding="utf-8") as log:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [node, str(SERVER_SCRIPT), str(checkout_path), str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            text=True,
        )
        try:
            async with asyncio.timeout(60):
                while not (tmp_path / "connection.json").exists():
                    assert process.poll() is None, (tmp_path / "sales.log").read_text()
                    await asyncio.sleep(0.1)
            connection = json.loads((tmp_path / "connection.json").read_text())
            settings = Settings(
                app_env="test",
                api_auth_mode="jwt",
                sales_integration_mode="erp_mcp",
                rate_limit_per_ip_per_minute=500,
                **connection["settings"],
            )
            engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chatbot.db'}")
            async with engine.begin() as db:
                await db.run_sync(SQLModel.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            transport = LoseCommittedResponse()
            mcp = SalesMcpClient(settings, transport)
            assert (await mcp.check_connection())["required_tools"] == 11
            # Test mode omits infrastructure startup. Install real persistent storage
            # and the actual network MCP client, exactly as production wiring does.
            app = create_app(
                Settings(
                    app_env="test",
                    api_auth_mode="jwt",
                    jwt_signing_key=settings.jwt_signing_key,
                    rate_limit_per_ip_per_minute=500,
                )
            )
            app.state.settings, app.state.session_factory = settings, sessions
            app.state.sales_service = SalesService(mcp, sessions)
            app.state.chat_service.thread_repository = ThreadRepository(sessions)
            app.state.chat_service.action_dispatcher.sales_service = app.state.sales_service
            app.state.chat_service.response_generator.sales_mode = True
            threads = [uuid4() for _ in connection["customer_ids"]]
            for thread, customer in zip(threads, connection["customer_ids"], strict=True):
                await app.state.chat_service.thread_repository.authorize(
                    thread_id=thread,
                    subject="sales-website-bridge",
                    customer_id=UUID(customer),
                )
            oracle = await asyncpg.connect(connection["database_url"])
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                    headers={"Authorization": "Bearer " + connection["customer_tokens"][0]},
                ) as client:
                    yield client, threads, connection, oracle, transport, sessions
            finally:
                await oracle.close()
                await mcp.aclose()
                await engine.dispose()
        finally:
            if process.poll() is None:
                assert process.stdin
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.stdin.close()
                await asyncio.to_thread(process.wait, timeout=30)
            assert process.returncode == 0, (tmp_path / "sales.log").read_text()


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


async def prepare(client, thread):
    base = f"/api/v1/customer/threads/{thread}/appointments"
    contact = {"name": "Synthetic Updated Customer", "phone": "+12025550124"}
    saved = checked(await client.post(base + "/contact", json=contact))
    assert checked(await client.post(base + "/contact", json=contact)) == saved
    day = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    slots = checked(
        await client.get(
            base + "/availability",
            params={
                "day": day,
                "timezone": "America/Chicago",
            },
        )
    )
    return base, saved, slots


def booking(slot):
    return {
        "appointment_id": str(uuid4()),
        "starts_at": slot["starts_at"],
        "customer_timezone": "America/Chicago",
        "csr_id": slot["csr_id"],
        "name": "Synthetic Updated Customer",
        "phone": "+12025550124",
    }


async def test_real_sales_booking_lifecycle_and_database_effects(real_sales):
    client, threads, connection, db, _, sessions = real_sales
    base, saved, availability = await prepare(client, threads[0])
    checked(
        await client.post(
            "/api/v1/chat/facts",
            json={
                "thread_id": str(threads[0]),
                "name": "Forged caller name",
                "source_label": "crm_sync",
            },
        )
    )
    state = await ThreadRepository(sessions).load_or_create(thread_id=threads[0])
    assert state.state.personal.name.value == "Synthetic Updated Customer"
    turn = checked(
        await client.post(
            "/api/v1/chat/turn",
            json={
                "thread_id": str(threads[0]),
                "customer_id": connection["customer_ids"][0],
                "message": "Hello, I need help editing my book.",
            },
        )
    )
    assert turn["thread_id"] == str(threads[0]) and isinstance(turn["bubbles"], list)
    slots = availability
    policy = checked(await client.get(base + "/policy"))
    assert policy["policy"]["enabled"] and "ownerIds" not in policy["policy"]
    payload = booking(slots[0])
    row = checked(await client.post(base, json=payload))
    assert row["status"] == "scheduled" and row["version"] == 1
    assert checked(await client.post(base, json=payload))["id"] == row["id"]
    checked(await client.post(base, json=booking(slots[-1])), 409)
    moved = checked(
        await client.post(
            base,
            json={
                **booking(slots[-1]),
                "appointment_id": row["id"],
                "expected_version": 1,
                "request_id": str(uuid4()),
            },
        )
    )
    assert moved["version"] == 2 and moved["status"] == "scheduled"
    checked(
        await client.delete(base + f"/{row['id']}?expected_version=1&request_id={uuid4()}"), 409
    )
    canceled = checked(
        await client.delete(base + f"/{row['id']}?expected_version=2&request_id={uuid4()}")
    )
    assert canceled["status"] == "canceled" and canceled["version"] == 3
    assert checked(await client.post(base, json=payload))["status"] == "canceled"
    assert (
        await db.fetchval(
            'SELECT count(*) FROM "Lead" WHERE "customerId"=$1', connection["customer_ids"][0]
        )
        == 1
    )
    assert (
        await db.fetchval('SELECT count(*) FROM "Consultation" WHERE "leadId"=$1', saved["lead_id"])
        == 1
    )
    assert await db.fetchval('SELECT count(*) FROM "Outbox" WHERE "aggregateId"=$1', row["id"]) == 3
    async with sessions() as session:
        assert not (await session.execute(select(SalesConsultationRecord))).scalars().all()
        assert not (await session.execute(select(DeliveryOutboxRecord))).scalars().all()


async def test_real_sales_lost_response_and_proposal_confirmation(real_sales):
    client, threads, _, db, transport, _ = real_sales
    base, _, availability = await prepare(client, threads[0])
    payload = booking(availability[0])
    transport.armed = True
    assert checked(await client.post(base, json=payload), 503)["retry_same_request"]
    operations = checked(await client.get(base + "/operations"))
    assert operations[0]["request_id"] == payload["appointment_id"]
    row = checked(await client.post(base + f"/operations/{payload['appointment_id']}/retry"))
    assert await db.fetchval('SELECT count(*) FROM "Consultation"') == 1
    checked(await client.delete(base + f"/{row['id']}?expected_version=1&request_id={uuid4()}"))
    # An off-grid timestamp produces a persisted offer, not a silent booking.
    when = datetime.fromisoformat(payload["starts_at"]) + timedelta(minutes=1)
    offered = checked(
        await client.post(
            base,
            json={
                **payload,
                "appointment_id": str(uuid4()),
                "starts_at": when.isoformat(),
            },
        )
    )
    assert offered["status"] == "proposed"
    confirmed = checked(
        await client.post(
            base + f"/{offered['id']}/confirm",
            json={
                "expected_version": 1,
                "request_id": str(uuid4()),
            },
        )
    )
    assert confirmed["status"] == "scheduled" and confirmed["version"] == 2


async def test_real_sales_customer_and_tenant_boundaries(real_sales):
    client, threads, connection, _, _, _ = real_sales
    base, _, _ = await prepare(client, threads[0])
    other_headers = {"Authorization": "Bearer " + connection["customer_tokens"][1]}
    checked(await client.get(base + "/contact", headers=other_headers), 403)
    foreign_headers = {"Authorization": "Bearer " + connection["customer_tokens"][2]}
    checked(
        await client.get(
            f"/api/v1/customer/threads/{threads[2]}/appointments/contact",
            headers=foreign_headers,
        ),
        404,
    )
    runtime = await asyncpg.connect(connection["runtime_database_url"])
    try:
        assert not await runtime.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert await runtime.fetchval('SELECT count(*) FROM "Customer"') == 2
    finally:
        await runtime.close()
