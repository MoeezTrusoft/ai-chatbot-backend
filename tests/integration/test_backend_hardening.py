import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from bookcraft.api.auth import AuthError, AuthPrincipal, verify_jwt_token
from bookcraft.api.chat import router
from bookcraft.api.main import build_chat_service
from bookcraft.api.thread_access import authorize_chat
from bookcraft.components.storage.action_idempotency_repository import ActionIdempotencyRepository
from bookcraft.components.storage.models import DeliveryOutboxRecord, utc_now
from bookcraft.components.storage.thread_repository import (
    ThreadRepository,
    ThreadVersionConflictError,
)
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings
from bookcraft.infra.rate_limit import InMemoryRateLimiter
from bookcraft.workers.csr_outbox import CsrOutboxWorker
from tests.unit.test_api_auth_unit import SIGNING_KEY, make_token


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "visitor"},
        {"sub": "visitor", "exp": "9999999999"},
        {"sub": "visitor", "exp": True},
        {"sub": "visitor", "exp": float("inf")},
    ],
)
def test_expiry_is_required_and_finite(claims):
    with pytest.raises(AuthError):
        verify_jwt_token(make_token(claims), Settings(jwt_signing_key=SIGNING_KEY))


async def test_identity_and_thread_ownership(factory):
    repo = ThreadRepository(factory)
    service = SimpleNamespace(thread_repository=repo)
    settings = Settings(app_env="test", api_auth_mode="jwt")
    first = AuthPrincipal(subject="visitor-a", customer_id=uuid4())
    other = AuthPrincipal(subject="visitor-b", customer_id=uuid4())
    tid, _ = await authorize_chat(first, settings, service, None, None)
    with pytest.raises(HTTPException) as exc:
        await authorize_chat(other, settings, service, tid, None)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        await authorize_chat(first, settings, service, None, other.customer_id)
    assert (await authorize_chat(first, settings, service, tid, None))[0] == tid


def test_staff_endpoints_reject_visitor_token():
    import time

    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(app_env="test", api_auth_mode="jwt", jwt_signing_key=SIGNING_KEY)
    headers = {"Authorization": "Bearer " + make_token({"sub": "visitor", "exp": time.time() + 60})}
    client = TestClient(app)
    tid = str(uuid4())
    assert client.get("/api/v1/chat/debug/state/" + tid, headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/chat/consultation-cancelled", json={"thread_id": tid}, headers=headers
        ).status_code
        == 403
    )


def test_stream_route_can_connect_and_send_validated_response():
    class Service:
        async def handle_turn(self, payload):
            from bookcraft.components.response.chat_schemas import ChatTurnResponse

            return ChatTurnResponse(
                thread_id=payload.thread_id, bubbles=[], intent=None, language_status="en"
            )

    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(app_env="test", response_streaming_enabled=True)
    app.state.chat_service = Service()
    app.state.rate_limiter = InMemoryRateLimiter(100)
    with TestClient(app).websocket_connect("/api/v1/chat/ws/stream") as ws:
        ws.send_json({"message": "hello"})
        assert ws.receive_json()["type"] == "stream_start"
        assert ws.receive_json()["type"] == "stream_end"
        assert ws.receive_json()["type"] == "response"


async def test_two_writers_cannot_both_commit_same_version(factory):
    repo = ThreadRepository(factory)
    thread = await repo.load_or_create(thread_id=None)
    results = await asyncio.gather(
        *[
            repo.save_state(
                thread_id=thread.thread_id,
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
    assert (await repo.load_or_create(thread_id=thread.thread_id)).turn_count == 1


async def test_action_replay_and_failed_retry_survive_new_repository(factory):
    first = ActionIdempotencyRepository(factory)
    args = dict(
        idempotency_key="stable-key",
        thread_id=uuid4(),
        action_type="create_lead",
        slots_hash="hash",
    )
    assert await first.claim(**args)
    assert not await ActionIdempotencyRepository(factory).claim(**args)
    await first.mark_failed(idempotency_key="stable-key", error_code="unavailable")
    assert await ActionIdempotencyRepository(factory).claim(**args)
    await first.mark_completed(idempotency_key="stable-key", result={"result_id": "stored"})
    fresh = ActionIdempotencyRepository(factory)
    assert not await fresh.claim(**args)
    assert await fresh.get_result(idempotency_key="stable-key") == {"result_id": "stored"}


async def test_application_factory_wires_optimizations_and_persistence(factory):
    flags = dict(
        event_log_batching_enabled=True,
        trg_background_persist_enabled=True,
        llm_extraction_overlap_enabled=True,
        trimatch_event_evidence_summary=True,
    )
    service = build_chat_service(Settings(app_env="test", **flags), session_factory=factory)
    assert service.action_dispatcher.action_idempotency_repository.session_factory is factory
    assert all(getattr(service, key) for key in flags)


async def test_outbox_retries_with_stable_delivery_key(factory):
    event = DeliveryOutboxRecord(
        aggregate_id=uuid4(), topic="consultation.upsert", payload={"name": "Synthetic"}
    )
    async with factory() as session:
        session.add(event)
        await session.commit()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        worker = CsrOutboxWorker(factory, "https://csr.example", client)
        assert await worker.deliver_one()
        assert not await worker.deliver_one()
        async with factory() as session:
            row = (await session.execute(select(DeliveryOutboxRecord))).scalar_one()
            assert row.completed_at is None and row.attempts == 1
            row.available_at = utc_now() - timedelta(seconds=1)
            await session.commit()
        assert await worker.deliver_one()
        assert not await worker.deliver_one()
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]


async def test_timeout_cancels_children_before_releasing_thread():
    from bookcraft.services.turn.execution import serialized_mutation, spawn_turn_task

    started, cancelled = asyncio.Event(), asyncio.Event()

    class Service:
        thread_repository = None
        turn_timeout_seconds = 0.1

        @serialized_mutation
        async def mutate(self, payload):
            async def background():
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            spawn_turn_task(background())
            await started.wait()
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await Service().mutate(SimpleNamespace(thread_id=uuid4()))
    assert cancelled.is_set()


async def test_state_and_buffered_events_rollback_together(factory):
    from bookcraft.components.storage.events import calculate_event_hash
    from bookcraft.components.storage.models import ThreadEvent

    repo = ThreadRepository(factory)
    thread = await repo.load_or_create(thread_id=None)
    payload = {"detail": "synthetic"}
    events = [
        {
            "sequence": 1,
            "event_type": "test",
            "payload": payload,
            "previous_hash": None,
            "event_hash": calculate_event_hash(
                thread_id=thread.thread_id,
                sequence=1,
                event_type="test",
                payload=payload,
                previous_hash=None,
            ),
        }
    ]
    with pytest.raises(ThreadVersionConflictError):
        await repo.save_state(
            thread_id=thread.thread_id,
            state=ThreadState(),
            expected_version=2,
            language="en",
            pending_events=events,
        )
    assert events
    async with factory() as session:
        assert not (await session.execute(select(ThreadEvent))).all()
    await repo.save_state(
        thread_id=thread.thread_id,
        state=ThreadState(),
        expected_version=0,
        language="en",
        pending_events=events,
    )
    assert not events
    async with factory() as session:
        assert len((await session.execute(select(ThreadEvent))).all()) == 1


async def test_outbox_preserves_aggregate_order_without_blocking_other_bookings(factory):
    aggregate = uuid4()
    first = DeliveryOutboxRecord(
        aggregate_id=aggregate,
        topic="consultation.upsert",
        payload={"revision": 1},
        created_at=utc_now() - timedelta(minutes=2),
        available_at=utc_now() + timedelta(hours=1),
    )
    later = DeliveryOutboxRecord(
        aggregate_id=aggregate,
        topic="consultation.upsert",
        payload={"revision": 2},
        created_at=utc_now() - timedelta(minutes=1),
    )
    unrelated = DeliveryOutboxRecord(
        aggregate_id=uuid4(),
        topic="consultation.upsert",
        payload={"revision": 3},
    )
    async with factory() as session:
        session.add_all([first, later, unrelated])
        await session.commit()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        worker = CsrOutboxWorker(factory, "https://csr.example", client)
        assert await worker.deliver_one()
        assert not await worker.deliver_one()
    assert requests[0].headers["Idempotency-Key"] == str(unrelated.id)


async def test_parallel_retrieval_preserves_approval_filter():
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.preprocessor.schemas import ProcessedMessage
    from bookcraft.components.rag.retriever import RagRetriever
    from bookcraft.domain.enums import QueryIntentType, ServiceCategory

    class Search:
        def __init__(self):
            self.calls = []
            self.both_started = asyncio.Event()

        async def search(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 2:
                self.both_started.set()
            await asyncio.wait_for(self.both_started.wait(), timeout=1)
            return {"hits": {"hits": []}}

    client = Search()
    retriever = RagRetriever(client, "test-index", vector_search_mode="knn")
    message = ProcessedMessage(
        raw="editing",
        normalized="editing",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[1.0, 0.0],
        language="en",
        char_count=7,
    )
    await retriever.retrieve(
        message,
        IntentVote(
            query_primary=QueryIntentType.SERVICE_QUESTION,
            service_primary=ServiceCategory.EDITING_PROOFREADING,
            funnel_stage="new",
            needs_clarification=False,
            confidence=1,
            rationale="Synthetic retrieval test",
        ),
    )
    assert len(client.calls) == 2
    vector = next(call for call in client.calls if "knn" in call)
    assert {"term": {"allowed_for_response": True}} in vector["knn"]["filter"]["bool"]["filter"]


def test_provider_usage_cost_counts_cached_tokens_once():
    import json

    from bookcraft.components.llm.metrics import (
        PROVIDER_COST,
        PROVIDER_TOKENS,
        configure_prices,
        record_usage,
    )

    model = str(uuid4())
    configure_prices({model: {"input": 2, "output": 4, "cache_read": 1}})
    record_usage(
        json.dumps(
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 40},
                }
            }
        ),
        provider="synthetic",
        model=model,
        purpose="test",
    )
    assert PROVIDER_TOKENS.labels("synthetic", model, "test", "input")._value.get() == 60
    assert PROVIDER_COST.labels("synthetic", model, "test")._value.get() == pytest.approx(0.00024)
    configure_prices({})


async def test_buffered_concurrent_turns_keep_a_complete_event_chain(factory):
    from bookcraft.components.response.chat_schemas import ChatTurnRequest
    from bookcraft.components.storage.models import ThreadEvent

    repo = ThreadRepository(factory)
    service = build_chat_service(
        Settings(
            app_env="test",
            event_log_batching_enabled=True,
            trg_background_persist_enabled=True,
            llm_extraction_overlap_enabled=True,
        ),
        session_factory=factory,
        thread_repository=repo,
    )
    tid = uuid4()
    await asyncio.gather(
        *[
            service.handle_turn(ChatTurnRequest(thread_id=tid, message=message))
            for message in ["Tell me about editing.", "My draft is 50000 words."]
        ]
    )
    loaded = await repo.load_or_create(thread_id=tid)
    assert loaded.version == 2
    async with factory() as session:
        events = (
            (
                await session.execute(
                    select(ThreadEvent)
                    .where(ThreadEvent.thread_id == tid)
                    .order_by(ThreadEvent.sequence)
                )
            )
            .scalars()
            .all()
        )
    assert events
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    previous = None
    for event in events:
        assert event.previous_hash == previous
        previous = event.event_hash


async def test_uncertain_action_is_not_automatically_retried(factory):
    repository = ActionIdempotencyRepository(factory)
    args = dict(
        idempotency_key="uncertain-action",
        thread_id=uuid4(),
        action_type="create_lead",
        slots_hash="hash",
    )
    assert await repository.claim(**args)
    await repository.mark_failed(
        idempotency_key="uncertain-action", error_code="transport_timeout", retryable=False
    )
    assert await repository.get_status(idempotency_key="uncertain-action") == "needs_reconciliation"
    assert not await ActionIdempotencyRepository(factory).claim(**args)
