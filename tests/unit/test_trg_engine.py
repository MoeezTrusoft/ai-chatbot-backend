from uuid import uuid4

import pytest

from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.components.trg import (
    InMemoryGraphRepository,
    RedisHotGraphStore,
    RelationType,
    TemporalRelationGraphEngine,
    TRGUpdateWorker,
)
from bookcraft.domain.enums import Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState
from bookcraft.infra.cache import CacheKeyBuilder
from bookcraft.tools import MemoryCache


@pytest.mark.asyncio
async def test_unresolved_question_persists_and_resolves_on_next_user_turn() -> None:
    thread_id = uuid4()
    engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

    first = await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="I need editing.",
        assistant_text="What is your word count?",
    )
    second = await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=2,
        user_text="About 60000 words.",
        assistant_text="Thanks.",
    )

    assert first.unresolved_question_count == 1
    assert second.unresolved_question_count == 0
    assert any(edge.relation_type == RelationType.ANSWERS for edge in second.added_edges)


@pytest.mark.asyncio
async def test_contradiction_edge_is_recorded_for_changed_state_fact() -> None:
    thread_id = uuid4()
    state = ThreadState()
    state.project.word_count = FieldMeta[int](
        value=50000,
        confidence=0.95,
        source=Source.USER_STATED,
        raw_excerpt="50000 words",
    )
    delta = StateDelta(
        path="project.word_count",
        value=70000,
        confidence=0.9,
        source=Source.USER_STATED,
        extracted_by="test",
        raw_excerpt="Actually 70000 words",
    )
    engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

    result = await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=2,
        user_text="Actually 70000 words.",
        assistant_text="Got it.",
        previous_state=state,
        state_deltas=[delta],
    )

    assert result.contradiction_count == 1
    assert any(edge.relation_type == RelationType.CONTRADICTS for edge in result.added_edges)
    assert result.graph.compliance_score == 0.6


@pytest.mark.asyncio
async def test_repetition_signal_counts_repeated_user_messages() -> None:
    thread_id = uuid4()
    engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="Can I see samples?",
        assistant_text="Which service samples do you want?",
    )
    result = await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=2,
        user_text="Can I see samples?",
        assistant_text="Yes.",
    )

    assert result.repetition_signal is not None
    assert result.repetition_signal.repeated is True
    # Self-edges (node → itself) are suppressed; the repetition signal alone is used downstream.
    assert not any(edge.relation_type == RelationType.REPEATS for edge in result.added_edges)


@pytest.mark.asyncio
async def test_graph_compaction_keeps_recent_nodes() -> None:
    thread_id = uuid4()
    repository = InMemoryGraphRepository()
    engine = TemporalRelationGraphEngine(repository=repository, compact_keep=6)

    for turn in range(10):
        await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=turn + 1,
            user_text=f"Message {turn}",
            assistant_text="Okay.",
        )
    graph = await repository.load(thread_id)

    assert graph is not None
    assert len(graph.nodes) <= 6
    assert all(edge.source_node_id in {node.id for node in graph.nodes} for edge in graph.edges)


@pytest.mark.asyncio
async def test_redis_hot_graph_store_round_trips_graph() -> None:
    thread_id = uuid4()
    cache = MemoryCache()
    store = RedisHotGraphStore(
        client=cache,
        keys=CacheKeyBuilder(environment="test"),
        ttl_seconds=60,
    )
    engine = TemporalRelationGraphEngine(repository=store)

    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="Hello",
        assistant_text="How can I help?",
    )
    loaded = await store.load(thread_id)

    assert loaded is not None
    assert loaded.thread_id == thread_id
    assert loaded.nodes


@pytest.mark.asyncio
async def test_trg_worker_retries_failed_update() -> None:
    attempts = 0
    thread_id = uuid4()
    engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

    async def job():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=1,
            user_text="Hi",
            assistant_text="Hello.",
        )

    result = await TRGUpdateWorker(max_attempts=2).run(job)

    assert attempts == 2
    assert result.graph.nodes
