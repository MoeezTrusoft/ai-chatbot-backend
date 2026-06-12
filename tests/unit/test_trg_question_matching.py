"""P2-T1 — question-to-answer matching (slot/embedding) in the TRG engine.

Validates that, with ``question_matching_enabled``:
  * a question resolves only when a state delta writes its slot, or the message
    embedding matches the question embedding;
  * greetings / short acks never resolve;
  * unrelated substantive turns leave the question open and bump ``ignored_count``;
  * every matching question resolves (not just the first);
  * ``TRGContext.questions_ignored`` surfaces the dodge signal;
  * with the flag OFF, the legacy "resolve the first question" behavior is intact.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.components.trg import (
    InMemoryGraphRepository,
    RelationType,
    TemporalRelationGraphEngine,
)
from bookcraft.components.trg.engine import _cosine, _derive_slot_path
from bookcraft.components.trg.schemas import TemporalRelationGraph, UnresolvedQuestion
from bookcraft.domain.enums import Source


def _delta(path: str, value: object) -> StateDelta:
    return StateDelta(
        path=path, value=value, confidence=0.9, source=Source.USER_STATED, extracted_by="test"
    )


class TestHelpers:
    def test_derive_slot_path_word_count(self) -> None:
        assert _derive_slot_path("What is your word count?") == "project.word_count"

    def test_derive_slot_path_genre(self) -> None:
        assert _derive_slot_path("And what genre is it?") == "project.genre"

    def test_derive_slot_path_none_for_unmapped(self) -> None:
        assert _derive_slot_path("How are you feeling today?") is None

    def test_cosine_identical_is_one(self) -> None:
        assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_cosine_orthogonal_is_zero(self) -> None:
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_cosine_empty_is_zero(self) -> None:
        assert _cosine([], [1.0]) == 0.0


class TestSlotMatching:
    @pytest.mark.asyncio
    async def test_slot_delta_resolves_matching_question(self) -> None:
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(
            repository=InMemoryGraphRepository(), question_matching_enabled=True
        )
        first = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="I need editing.", assistant_text="What is your word count?",
        )
        assert first.unresolved_question_count == 1

        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="About 60000 words for the manuscript.", assistant_text="Thanks.",
            state_deltas=[_delta("project.word_count", 60000)],
        )
        assert second.unresolved_question_count == 0
        assert any(e.relation_type == RelationType.ANSWERS for e in second.added_edges)

    @pytest.mark.asyncio
    async def test_unrelated_turn_leaves_question_open_and_bumps_ignored(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repo, question_matching_enabled=True)
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="I need editing.", assistant_text="What is your word count?",
        )
        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="Tell me about your portfolio please.", assistant_text="Sure.",
            state_deltas=[],  # no slot delta, no embedding → no resolution
        )
        assert second.unresolved_question_count == 1
        graph = await repo.load(thread_id)
        open_q = next(q for q in graph.unresolved_questions if not q.resolved)
        assert open_q.ignored_count == 1

    @pytest.mark.asyncio
    async def test_greeting_never_resolves(self) -> None:
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(
            repository=InMemoryGraphRepository(), question_matching_enabled=True
        )
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="I need editing.", assistant_text="What is your word count?",
        )
        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="hi", assistant_text="Hello!",
            state_deltas=[_delta("project.word_count", 60000)],  # even with a delta, "hi" is guarded
        )
        assert second.unresolved_question_count == 1

    @pytest.mark.asyncio
    async def test_only_matching_question_resolves(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repo, question_matching_enabled=True)
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="I need help.",
            assistant_text="What is your word count? What genre is it?",
        )
        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="It is around 80000 words total.", assistant_text="Great.",
            state_deltas=[_delta("project.word_count", 80000)],
        )
        # word_count question resolved; genre question still open with ignored bumped.
        assert second.unresolved_question_count == 1
        graph = await repo.load(thread_id)
        genre_q = next(q for q in graph.unresolved_questions if q.slot_path == "project.genre")
        assert genre_q.resolved is False
        assert genre_q.ignored_count == 1

    @pytest.mark.asyncio
    async def test_resolves_every_matching_question(self) -> None:
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(
            repository=InMemoryGraphRepository(), question_matching_enabled=True
        )
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="I need help.",
            assistant_text="What is your word count? What genre is it?",
        )
        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="It is an 80000 word fantasy novel.", assistant_text="Great.",
            state_deltas=[_delta("project.word_count", 80000), _delta("project.genre", "fantasy")],
        )
        assert second.unresolved_question_count == 0


class TestEmbeddingMatching:
    @pytest.mark.asyncio
    async def test_embedding_similarity_resolves(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(
            repository=repo, question_matching_enabled=True, answer_match_threshold=0.6
        )
        # Seed a graph with an embedded question (no slot path).
        graph = TemporalRelationGraph(thread_id=thread_id)
        graph.unresolved_questions.append(
            UnresolvedQuestion(
                node_id=uuid4(), question="Tell me more?", asked_turn_sequence=1,
                embedding=[1.0, 0.0, 0.0],
            )
        )
        await repo.save(graph)

        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="Here is a real and detailed answer.", assistant_text="Thanks.",
            user_embedding=[0.98, 0.02, 0.0],  # cosine ≈ 1.0 ≥ 0.6
        )
        assert second.unresolved_question_count == 0

    @pytest.mark.asyncio
    async def test_dissimilar_embedding_does_not_resolve(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(
            repository=repo, question_matching_enabled=True, answer_match_threshold=0.6
        )
        graph = TemporalRelationGraph(thread_id=thread_id)
        graph.unresolved_questions.append(
            UnresolvedQuestion(
                node_id=uuid4(), question="Tell me more?", asked_turn_sequence=1,
                embedding=[1.0, 0.0, 0.0],
            )
        )
        await repo.save(graph)
        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="A completely unrelated statement here.", assistant_text="Ok.",
            user_embedding=[0.0, 1.0, 0.0],  # cosine 0.0
        )
        assert second.unresolved_question_count == 1


class TestContextSignal:
    @pytest.mark.asyncio
    async def test_questions_ignored_surfaces_in_context(self) -> None:
        thread_id = uuid4()
        repo = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repo, question_matching_enabled=True)
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="I need editing.", assistant_text="What is your word count?",
        )
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="Tell me about your portfolio first.", assistant_text="Sure.",
        )
        graph = await repo.load(thread_id)
        ctx = engine.build_context(graph)
        assert ctx.questions_ignored == 1


class TestLegacyBehaviorPreserved:
    @pytest.mark.asyncio
    async def test_flag_off_resolves_first_question_legacy(self) -> None:
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(
            repository=InMemoryGraphRepository(), question_matching_enabled=False
        )
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=1,
            user_text="I need editing.", assistant_text="What is your word count?",
        )
        # No delta, no embedding — legacy path still resolves on any substantive text.
        second = await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=2,
            user_text="Tell me about your portfolio please.", assistant_text="Sure.",
        )
        assert second.unresolved_question_count == 0
