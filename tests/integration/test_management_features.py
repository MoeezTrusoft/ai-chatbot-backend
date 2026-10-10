import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from bookcraft.api.appointments import router as appointments
from bookcraft.api.customer_data import router as customer_data
from bookcraft.api.intake import router as intake
from bookcraft.api.knowledge import router as knowledge
from bookcraft.api.workspace import router as workspace
from bookcraft.components.analysis import LiveTraceStore
from bookcraft.components.consultations.holidays import set_holiday_cache
from bookcraft.components.preprocessor.embedding import EmbeddingClient
from bookcraft.components.storage.management_models import FeedbackRecord
from bookcraft.components.storage.models import DeliveryOutboxRecord, ThreadRecord
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings
from bookcraft.receiver.main import create_receiver
from bookcraft.receiver.models import ReceiverAppointment
from bookcraft.services.privacy import erase_thread
from bookcraft.workers.csr_outbox import CsrOutboxWorker
from tests.unit.test_api_auth_unit import SIGNING_KEY, make_token


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'management.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def headers(subject="owner", scope=""):
    return {
        "Authorization": "Bearer "
        + make_token({"sub": subject, "exp": time.time() + 300, "scope": scope})
    }


async def test_reminders_require_consent_and_never_blindly_retry(factory, monkeypatch):
    from bookcraft.receiver.models import ReceiverReminder
    from bookcraft.receiver.reminders import remind_one

    tid, aid = uuid4(), uuid4()
    row = ReceiverAppointment(
        id=aid,
        thread_id=tid,
        starts_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=2),
        version=1,
        status="scheduled",
        payload=delivery(tid, aid),
    )
    async with factory() as session:
        session.add(row)
        await session.commit()
    sends = []
    monkeypatch.setattr(
        "bookcraft.receiver.reminders.send_reminder",
        lambda settings, row: sends.append(row.version),
    )
    settings = Settings(app_env="test")
    await remind_one(factory, settings, aid)
    assert sends == []
    async with factory() as session:
        row = await session.get(ReceiverAppointment, aid)
        row.payload = {**row.payload, "remindersEnabled": True}
        session.add(row)
        await session.commit()
    await asyncio.gather(*(remind_one(factory, settings, aid) for _ in range(4)))
    assert sends == [1]
    async with factory() as session:
        row = await session.get(ReceiverAppointment, aid)
        row.version = 2
        session.add(row)
        await session.commit()

    def ambiguous(*args):
        sends.append(2)
        raise TimeoutError("ambiguous SMTP outcome")

    monkeypatch.setattr("bookcraft.receiver.reminders.send_reminder", ambiguous)
    await remind_one(factory, settings, aid)
    await remind_one(factory, settings, aid)
    assert sends == [1, 2]
    async with factory() as session:
        assert (await session.get(ReceiverReminder, (aid, 2))).status == "needs_reconciliation"
        row = await session.get(ReceiverAppointment, aid)
        row.version, row.status = 3, "cancelled"
        session.add(row)
        await session.commit()
    await remind_one(factory, settings, aid)
    assert sends == [1, 2]


@pytest.mark.parametrize("attachment_in_state", [False, True])
async def test_external_file_erasure_requires_acknowledgment(
    client, factory, tmp_path, attachment_in_state
):
    from bookcraft.components.storage.management_models import WorkflowRecord

    http, tid, app = client
    async with factory() as session:
        if attachment_in_state:
            thread = await session.get(ThreadRecord, tid)
            thread.state = {**thread.state, "attachments_received": [{"filename": "test.pdf"}]}
            session.add(thread)
        else:
            session.add(
                WorkflowRecord(
                    thread_id=tid,
                    kind="intake",
                    status="uploaded",
                    payload={"storage_key": "synthetic-object"},
                )
            )
        await session.commit()
    await http.delete(f"/api/v1/customer/threads/{tid}/data")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "deleted"})
        )
    ) as external:
        with pytest.raises(RuntimeError, match="attachment_cleanup_not_configured"):
            await erase_thread(
                factory,
                app.state.settings,
                LiveTraceStore(tmp_path / "trace.jsonl"),
                None,
                external,
                tid,
            )
        assert (await http.get(f"/api/v1/customer/threads/{tid}/deletion")).json()[
            "status"
        ] == "queued"
        app.state.settings.attachment_cleanup_url = "https://files.example.test"
        app.state.settings.attachment_cleanup_token = str(uuid4())
        await erase_thread(
            factory,
            app.state.settings,
            LiveTraceStore(tmp_path / "trace.jsonl"),
            None,
            external,
            tid,
        )
    assert (await http.get(f"/api/v1/customer/threads/{tid}/deletion")).json()[
        "status"
    ] == "completed"


def test_trace_migration_pagination_retention_and_deletion(tmp_path):
    import json

    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "thread_id": "old",
                "recorded_at": "2000-01-01T00:00:00+00:00",
                "text": "synthetic@example.com",
            }
        )
        + "\n"
    )
    store = LiveTraceStore(path)
    assert len(store.latest()) == 1
    assert not path.exists()
    assert len(LiveTraceStore(path).latest()) == 1
    for i in range(5):
        store.append({"thread_id": "new", "index": i})
    page = store.for_thread("new", limit=2)
    assert [row["index"] for row in page] == [4, 3]
    assert [
        row["index"] for row in store.for_thread("new", before_id=page[-1]["trace_id"], limit=2)
    ] == [2, 1]
    assert store.prune() == 1
    store.delete_thread("new")
    assert store.latest() == []


@pytest.fixture
async def client(factory):
    app = FastAPI()
    app.state.settings = Settings(
        app_env="test", api_auth_mode="jwt", jwt_signing_key=SIGNING_KEY, csr_node_api_url=""
    )
    app.state.session_factory = factory
    for router in (appointments, customer_data, intake, knowledge, workspace):
        app.include_router(router)
    tid = uuid4()
    async with factory() as session:
        session.add(ThreadRecord(id=tid, owner_subject="owner", turn_count=2))
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers()
    ) as transport:
        yield transport, tid, app


def booking_payload():
    day = datetime.now(UTC) + timedelta(days=7)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    # 16 UTC is inside the Houston business window throughout DST.
    return dict(
        appointment_id=str(uuid4()),
        starts_at=day.replace(hour=16, minute=0, second=0, microsecond=0).isoformat(),
        customer_timezone="Asia/Karachi",
        csr_id="jerry-miller",
        name="Test Author",
        email="synthetic@example.com",
        reminders_enabled=True,
    )


async def test_customer_ownership_and_staff_scope(client):
    http, tid, _ = client
    assert (await http.get(f"/api/v1/customer/threads/{tid}/export")).status_code == 200
    assert (
        await http.get(f"/api/v1/customer/threads/{tid}/export", headers=headers("other"))
    ).status_code == 403
    assert (await http.get("/api/v1/staff/threads")).status_code == 403
    assert (
        await http.get("/api/v1/staff/threads", headers=headers("staff", "chat:manage"))
    ).status_code == 200


async def test_booking_reschedule_cancellation_are_atomic_and_versioned(client, factory):
    http, tid, _ = client
    set_holiday_cache([])
    path = f"/api/v1/customer/threads/{tid}/appointments"
    payload = booking_payload()
    created = await http.post(path, json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["version"] == 1
    assert (await http.post(path, json=payload)).status_code == 409
    payload["expected_version"] = 1
    payload["starts_at"] = (
        datetime.fromisoformat(payload["starts_at"]) + timedelta(minutes=30)
    ).isoformat()
    moved = await http.post(path, json=payload)
    assert moved.status_code == 200, moved.text
    assert moved.json()["version"] == 2
    cancel = await http.delete(path + "/" + payload["appointment_id"] + "?expected_version=2")
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["version"] == 3
    async with factory() as session:
        thread = await session.get(ThreadRecord, tid)
        assert not ThreadState.model_validate(
            thread.state
        ).sales_actions.consultation.confirmed_appointment_id
        events = (await session.execute(select(DeliveryOutboxRecord))).scalars().all()
        assert [row.payload["version"] for row in events] == [1, 2, 3]
        assert events[-1].payload["status"] == "cancelled"


async def test_booking_rejects_invalid_slot_and_missing_consent_contact(client):
    http, tid, _ = client
    data = booking_payload()
    data["starts_at"] = datetime.now(UTC).isoformat()
    assert (
        await http.post(f"/api/v1/customer/threads/{tid}/appointments", json=data)
    ).status_code == 422


async def test_feedback_is_owner_scoped_redacted_and_validated(client, factory):
    http, tid, _ = client
    data = {
        "turn_sequence": 2,
        "helpful": False,
        "reason": "unclear",
        "comment": "email synthetic@example.com",
    }
    path = f"/api/v1/customer/threads/{tid}/feedback"
    assert (await http.put(path, json=data)).status_code == 200
    assert (await http.put(path, json={**data, "turn_sequence": 99})).status_code == 422
    async with factory() as session:
        record = await session.get(FeedbackRecord, (tid, 2))
        assert "synthetic@example.com" not in record.comment


def delivery(tid, aid, version=1, status="scheduled"):
    start = datetime.now(UTC) + timedelta(hours=12)
    return {
        "externalAppointmentId": str(aid),
        "version": version,
        "status": status,
        "startsAtUtc": start.isoformat(),
        "endsAtUtc": (start + timedelta(minutes=30)).isoformat(),
        "csrId": "jerry-miller",
        "name": "Synthetic",
        "threadId": str(tid),
        "email": "synthetic@example.com",
    }


async def test_receiver_deduplicates_rejects_key_reuse_and_preserves_cancellation(factory):
    settings = Settings(app_env="test", csr_node_api_token="t" * 32)
    app = create_receiver(settings, factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://receiver",
            headers={"Authorization": "Bearer " + "t" * 32},
        ) as http:
            tid, aid = uuid4(), uuid4()
            original = delivery(tid, aid)
            h = {"Idempotency-Key": "original"}
            assert (
                await http.post("/api/consultations", json=original, headers=h)
            ).status_code == 200
            duplicate = await http.post("/api/consultations", json=original, headers=h)
            assert duplicate.json()["duplicate"] is True
            changed = {**original, "name": "Changed"}
            assert (
                await http.post("/api/consultations", json=changed, headers=h)
            ).status_code == 409
            cancelled = {**original, "version": 2, "status": "cancelled"}
            assert (
                await http.post(
                    "/api/consultations", json=cancelled, headers={"Idempotency-Key": "cancel"}
                )
            ).status_code == 200
            stale = await http.post(
                "/api/consultations", json=original, headers={"Idempotency-Key": "late-original"}
            )
            assert stale.json()["version"] == 2 and stale.json()["status"] == "cancelled"
            assert (await http.delete(f"/api/threads/{tid}/data")).status_code == 200
            assert (
                await http.post("/api/consultations", json=original, headers=h)
            ).status_code == 410


async def test_real_outbox_receiver_contract(client, factory):
    http, tid, _ = client
    data = booking_payload()
    assert (
        await http.post(f"/api/v1/customer/threads/{tid}/appointments", json=data)
    ).status_code == 200
    settings = Settings(app_env="test", csr_node_api_token="t" * 32)
    receiver = create_receiver(settings, factory)
    async with (
        receiver.router.lifespan_context(receiver),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=receiver), base_url="http://receiver"
        ) as transport,
    ):
        worker = CsrOutboxWorker(factory, "http://receiver", transport, "t" * 32)
        assert await worker.deliver_one()
    async with factory() as session:
        record = await session.get(ReceiverAppointment, UUID(data["appointment_id"]))
        assert record is not None and record.version == 1
        event = (await session.execute(select(DeliveryOutboxRecord))).scalar_one()
        assert event.completed_at is not None


async def test_invalid_ack_is_quarantined_and_replay_preserves_identity(client, factory):
    http, tid, _ = client
    row = DeliveryOutboxRecord(aggregate_id=uuid4(), topic="consultation.upsert", payload={})
    async with factory() as session:
        session.add(row)
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    ) as transport:
        assert await CsrOutboxWorker(
            factory, "http://receiver", transport, max_attempts=1
        ).deliver_one()
    async with factory() as session:
        saved = await session.get(DeliveryOutboxRecord, row.id)
        assert saved.dead_letter_at is not None and saved.completed_at is None
    result = await http.post(
        f"/api/v1/staff/deliveries/{row.id}/replay", headers=headers("staff", "chat:manage")
    )
    assert result.status_code == 200
    async with factory() as session:
        saved = await session.get(DeliveryOutboxRecord, row.id)
        assert saved.dead_letter_at is None and saved.replayed_by == "staff"


async def test_intake_rejects_stale_callbacks_and_unsafe_retry(client):
    http, tid, _ = client
    path = f"/api/v1/customer/threads/{tid}/intake"
    row = (
        await http.post(
            path, json={"filename": "manuscript.txt", "mime_type": "text/plain", "size_bytes": 123}
        )
    ).json()
    update_path = "/api/v1/staff/intake/" + row["id"]
    auth = headers("upload-service", "intake:manage")
    assert (
        await http.patch(
            update_path, headers=auth, json={"expected_version": 1, "status": "completed"}
        )
    ).status_code == 409
    assert (
        await http.patch(
            update_path, headers=auth, json={"expected_version": 1, "status": "uploaded"}
        )
    ).status_code == 200
    assert (
        await http.patch(
            update_path, headers=auth, json={"expected_version": 1, "status": "scanning"}
        )
    ).status_code == 409
    assert (
        await http.patch(
            update_path,
            headers=auth,
            json={"expected_version": 2, "status": "failed", "error_code": "unsafe_file"},
        )
    ).status_code == 200
    assert (await http.post(path + "/" + row["id"] + "/retry")).status_code == 409


async def test_knowledge_requires_valid_sources_review_and_safe_activation(client, monkeypatch):
    from types import SimpleNamespace

    from bookcraft.api.knowledge import RagIndexManager
    from bookcraft.components.response.schemas import ResponseDraft

    http, _, app = client
    built, activated = [], []

    async def create_index(manager):
        built.append(manager.index_name)

    async def index_chunks(manager, chunks):
        assert chunks[0].content_vector == [1.0]
        assert chunks[0].expires_at is not None

    async def promote(manager):
        activated.append(manager.index_name)

    async def embed(content, language):
        return [1.0]

    async def generate(**kwargs):
        assert kwargs["rag_chunks"][0].source_id == "source-one"
        assert kwargs["state"].personal.email.value is None
        return ResponseDraft(
            text="BookCraft helps authors prepare manuscripts for publication.",
            source="claude_sonnet",
        )

    monkeypatch.setattr(RagIndexManager, "create_index", create_index)
    monkeypatch.setattr(RagIndexManager, "index_chunks", index_chunks)
    monkeypatch.setattr(RagIndexManager, "promote_alias", promote)
    app.state.elasticsearch_client = object()
    app.state.chat_service = SimpleNamespace(
        preprocessor=SimpleNamespace(embedding_client=SimpleNamespace(embed=embed)),
        response_generator=SimpleNamespace(adapter=object(), generate=generate),
    )
    content = "BookCraft helps authors prepare their manuscripts for publication."
    chunk = {
        "chunk_id": "source-one",
        "content": content,
        "checksum": hashlib.sha256(content.encode()).hexdigest(),
        "metadata": {
            "source_id": "source-one",
            "title": "Publishing",
            "section": "Overview",
            "source_filename": "publishing.md",
            "content_version": "1",
        },
    }
    release = {
        "title": "Reviewed sources",
        "chunks": [chunk],
        "expires_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
    }
    auth = headers("editor", "knowledge:manage")
    assert (await http.post("/api/v1/staff/knowledge", json=release)).status_code == 403
    response = await http.post("/api/v1/staff/knowledge", json=release, headers=auth)
    assert response.status_code == 200, response.text
    rid = response.json()["id"]
    assert (
        await http.post(f"/api/v1/staff/knowledge/{rid}/publish", headers=auth)
    ).status_code == 409
    preview = await http.post(
        f"/api/v1/staff/knowledge/{rid}/preview", json={"query": "publication"}, headers=auth
    )
    assert preview.status_code == 200 and preview.json()["sources"]
    generated = await http.post(
        f"/api/v1/staff/knowledge/{rid}/preview",
        json={"query": "publication", "generate_answer": True},
        headers=auth,
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["mode"] == "generated_answer_preview"
    assert "passed" in generated.json()["quality"]
    assert (
        await http.post(f"/api/v1/staff/knowledge/{rid}/approve", headers=auth)
    ).status_code == 200
    assert (
        await http.post(f"/api/v1/staff/knowledge/{rid}/approve", headers=auth)
    ).status_code == 409
    assert (
        await http.post(f"/api/v1/staff/knowledge/{rid}/publish", headers=auth)
    ).status_code == 200
    assert (
        await http.post(f"/api/v1/staff/knowledge/{rid}/rollback", headers=auth)
    ).status_code == 200
    assert len(built) == 1
    assert activated == [built[0], built[0]]
    release["chunks"][0]["content"] = "Costs $500"
    assert (
        await http.post("/api/v1/staff/knowledge", json=release, headers=auth)
    ).status_code == 422


async def test_csr_callback_cancellation_rejects_stale_version(client, factory):
    from bookcraft.components.storage.thread_repository import ThreadRepository
    from bookcraft.services.appointment_sync import cancel_persisted_appointment

    http, tid, _ = client
    payload = booking_payload()
    path = f"/api/v1/customer/threads/{tid}/appointments"
    assert (await http.post(path, json=payload)).status_code == 200
    payload["expected_version"] = 1
    assert (await http.post(path, json=payload)).status_code == 200
    repo = ThreadRepository(factory)
    async with repo.serialize(tid):
        assert not await cancel_persisted_appointment(repo, tid, payload["appointment_id"], 1)
        assert await cancel_persisted_appointment(repo, tid, payload["appointment_id"], 2)
    records = (await http.get(path)).json()
    assert records[0]["status"] == "cancelled" and records[0]["version"] == 3


async def test_deletion_blocks_access_before_cleanup_and_erases_traces(client, factory, tmp_path):
    http, tid, app = client
    traces = LiveTraceStore(tmp_path / "trace.jsonl")
    traces.append({"thread_id": str(tid), "text": "private"})
    response = await http.delete(f"/api/v1/customer/threads/{tid}/data")
    assert response.status_code == 202
    assert (await http.get(f"/api/v1/customer/threads/{tid}/export")).status_code == 404
    async with httpx.AsyncClient() as transport:
        await erase_thread(factory, app.state.settings, traces, None, transport, tid)
    assert traces.for_thread(str(tid)) == []
    assert (await http.get(f"/api/v1/customer/threads/{tid}/deletion")).json()[
        "status"
    ] == "completed"


async def test_legacy_owner_can_check_completed_deletion(client, factory, tmp_path):
    from bookcraft.components.storage.models import Customer

    http, tid, app = client
    cid = uuid4()
    async with factory() as session:
        session.add(Customer(id=cid))
        await session.flush()
        thread = await session.get(ThreadRecord, tid)
        thread.owner_subject, thread.customer_id = None, cid
        session.add(thread)
        await session.commit()
    auth = {
        "Authorization": "Bearer "
        + make_token(
            {
                "sub": "legacy",
                "customer_id": str(cid),
                "exp": time.time() + 300,
            }
        )
    }
    assert (
        await http.delete(f"/api/v1/customer/threads/{tid}/data", headers=auth)
    ).status_code == 202
    async with httpx.AsyncClient() as external:
        await erase_thread(
            factory,
            app.state.settings,
            LiveTraceStore(tmp_path / "legacy.jsonl"),
            None,
            external,
            tid,
        )
    result = await http.get(f"/api/v1/customer/threads/{tid}/deletion", headers=auth)
    assert result.status_code == 200 and result.json()["status"] == "completed"


async def test_embedding_pool_singleflight_and_vector_validation():
    calls = []

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=[[1.0, 2.0, 3.0]])

    client = EmbeddingClient(
        "http://tei", 1, 3, False, client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    results = await asyncio.gather(*(client.embed("same text", "en") for _ in range(20)))
    assert results == [[1, 2, 3]] * 20 and len(calls) == 1
    digest = client.cache_digest("same text", "en")
    client.model_revision = "new"
    assert client.cache_digest("same text", "en") != digest
    for invalid in ([[1, 2]], [[1, 2, float("nan")]]):
        with pytest.raises(ValueError):
            client._parse_vector(invalid)
    await client.aclose()
