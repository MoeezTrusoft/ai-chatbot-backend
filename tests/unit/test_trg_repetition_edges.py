"""P2-T7 — repetition edges link to the prior occurrence node.

With ``repetition_edges_v2`` enabled, a repeated message produces a queryable
``REPEATS`` edge from the new node to the FIRST occurrence's node (not a self-edge).
With the flag off, no REPEATS edge is created (the prior, fixed behavior).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.trg import (
    InMemoryGraphRepository,
    RelationType,
    TemporalRelationGraphEngine,
)


class TestRepetitionEdgesV2:
    @pytest.mark.asyncio
    async def test_repeat_creates_edge_to_prior_occurrence(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repo, repetition_edges_v2=True)

        first = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="How much does publishing cost?", assistant_text="It depends.",
        )
        # First occurrence: no REPEATS edge.
        assert not any(e.relation_type == RelationType.REPEATS for e in first.added_edges)

        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="How much does publishing cost?", assistant_text="As I said, it depends.",
        )
        repeats = [e for e in second.added_edges if e.relation_type == RelationType.REPEATS]
        assert len(repeats) == 1
        # Edge is to a DIFFERENT (prior) node — never a self-loop.
        assert repeats[0].source_node_id != repeats[0].target_node_id

    @pytest.mark.asyncio
    async def test_prior_node_id_recorded(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repo, repetition_edges_v2=True)
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="What is the timeline?", assistant_text="Depends on scope.",
        )
        graph = await repo.load(thread_id)
        assert len(graph.repetition_first_node_id) == 1

    @pytest.mark.asyncio
    async def test_edge_target_is_first_occurrence(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repo, repetition_edges_v2=True)
        first = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="Can you do audiobooks?", assistant_text="Yes.",
        )
        first_user_node = next(n for n in first.added_nodes if n.text == "Can you do audiobooks?")
        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="Can you do audiobooks?", assistant_text="Yes, we can.",
        )
        repeat_edge = next(e for e in second.added_edges if e.relation_type == RelationType.REPEATS)
        assert repeat_edge.target_node_id == first_user_node.id


class TestRepetitionEdgesFlagOff:
    @pytest.mark.asyncio
    async def test_no_repeats_edge_when_disabled(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repo, repetition_edges_v2=False)
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="How much does it cost?", assistant_text="Depends.",
        )
        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="How much does it cost?", assistant_text="Depends.",
        )
        assert not any(e.relation_type == RelationType.REPEATS for e in second.added_edges)
        # Repetition is still COUNTED even with the edge feature off.
        assert second.repetition_signal is not None
        assert second.repetition_signal.repeated is True

    @pytest.mark.asyncio
    async def test_no_first_node_map_when_disabled(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repo, repetition_edges_v2=False)
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="Hello there friend.", assistant_text="Hi.",
        )
        graph = await repo.load(thread_id)
        assert graph.repetition_first_node_id == {}
