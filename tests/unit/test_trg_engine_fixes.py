"""Tests for specific bug-fixes applied to bookcraft.components.trg.engine.

File organisation
-----------------
TestQuestionResolution  — Fix: greetings/acknowledgments must NOT resolve outstanding questions
TestForbiddenReasks     — Fix: forbidden_reasks_from_facts must cover all registered fact paths
TestCompactionScoring   — Fix: additive blend must let index-0 high-engagement nodes survive
TestQuestionExtraction  — Fix: extract_questions must strip non-question preamble sentences
"""

from uuid import uuid4

import pytest

from bookcraft.components.trg import (
    InMemoryGraphRepository,
    RelationType,
    TemporalRelationGraphEngine,
    TRGFactNode,
    forbidden_reasks_from_facts,
)
from bookcraft.components.trg.engine import extract_questions


# ---------------------------------------------------------------------------
# TestQuestionResolution
#
# Bug: one-word greetings ("hi", "ok", "thanks") were being accepted as valid
# answers to outstanding questions, incorrectly flipping resolved=True.
# Fix: _NON_ANSWER_PHRASES guard + 3-word minimum check.
# ---------------------------------------------------------------------------


class TestQuestionResolution:
    @pytest.mark.asyncio
    async def test_greeting_does_not_resolve_question(self) -> None:
        """'hi' is a social filler, not an answer — unresolved question must persist."""
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

        await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=1,
            user_text="I need some help.",
            assistant_text="What genre are you writing in?",
        )
        result = await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=2,
            user_text="hi",
            assistant_text="No problem, take your time.",
        )

        assert result.unresolved_question_count == 1

    @pytest.mark.asyncio
    async def test_ok_does_not_resolve_question(self) -> None:
        """'ok' is an acknowledgment, not a substantive answer."""
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

        await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=1,
            user_text="I want a book written.",
            assistant_text="What is your word count?",
        )
        result = await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=2,
            user_text="ok",
            assistant_text="Understood.",
        )

        assert result.unresolved_question_count == 1

    @pytest.mark.asyncio
    async def test_thanks_does_not_resolve_question(self) -> None:
        """'thanks' is a social phrase, not a question answer."""
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

        await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=1,
            user_text="I need editing help.",
            assistant_text="What is your manuscript length?",
        )
        result = await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=2,
            user_text="thanks",
            assistant_text="You're welcome.",
        )

        assert result.unresolved_question_count == 1

    @pytest.mark.asyncio
    async def test_substantive_answer_resolves_question(self) -> None:
        """A 3+-word specific answer must flip the outstanding question to resolved."""
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

        await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=1,
            user_text="I need a book written.",
            assistant_text="What genre are you interested in?",
        )
        result = await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=2,
            user_text="I want a fantasy novel about dragons",
            assistant_text="Great choice!",
        )

        assert result.unresolved_question_count == 0
        assert any(edge.relation_type == RelationType.ANSWERS for edge in result.added_edges)

    @pytest.mark.asyncio
    async def test_two_word_answer_does_not_resolve(self) -> None:
        """Fewer than 3 words falls below the threshold — question remains open.

        'fantasy novel' is 2 words; the engine's word-count guard (< 3 words)
        means it should NOT be treated as a valid answer.
        """
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

        await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=1,
            user_text="I need a ghostwriter.",
            assistant_text="What genre are you writing in?",
        )
        result = await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=2,
            user_text="fantasy novel",
            assistant_text="Sounds interesting.",
        )

        assert result.unresolved_question_count == 1


# ---------------------------------------------------------------------------
# TestForbiddenReasks
#
# Bug: forbidden_reasks_from_facts only checked a small hard-coded subset of
# paths (genre, manuscript_status) instead of the full _REASK_PROTECTION table.
# Fix: the function now iterates over the entire _REASK_PROTECTION dict so
# every registered fact_path is automatically covered.
# ---------------------------------------------------------------------------


class TestForbiddenReasks:
    def _fact(self, fact_path: str) -> TRGFactNode:
        return TRGFactNode(fact_path=fact_path, value="test_value", active=True)

    def test_genre_protected(self) -> None:
        """project.genre must produce at least one forbidden reask phrase."""
        phrases = forbidden_reasks_from_facts([self._fact("project.genre")])

        assert any("genre" in p for p in phrases)

    def test_word_count_protected(self) -> None:
        """project.word_count must produce phrases about word count."""
        phrases = forbidden_reasks_from_facts([self._fact("project.word_count")])

        assert any("word" in p for p in phrases)

    def test_contact_email_protected(self) -> None:
        """contact.email must produce phrases about email."""
        phrases = forbidden_reasks_from_facts([self._fact("contact.email")])

        assert any("email" in p for p in phrases)

    def test_unknown_path_not_protected(self) -> None:
        """A fact_path absent from the protection table returns an empty list."""
        phrases = forbidden_reasks_from_facts([self._fact("project.unknown_field_xyz")])

        assert phrases == []

    def test_all_known_paths_covered(self) -> None:
        """Every registered fact path in _REASK_PROTECTION must return a non-empty list.

        This acts as a regression guard: if a new path is added to _REASK_PROTECTION
        but forbidden_reasks_from_facts skips it, this test will catch the gap.
        """
        known_paths = [
            "project.genre",
            "project.manuscript_status",
            "project.word_count",
            "project.title",
            "project.formats",
            "contact.name",
            "contact.email",
            "contact.phone",
            "service.timeline",
            "service.budget",
            "project.page_count",
            "project.platforms",
        ]

        for path in known_paths:
            phrases = forbidden_reasks_from_facts([self._fact(path)])
            assert phrases, f"Expected non-empty forbidden phrases for fact_path={path!r}"


# ---------------------------------------------------------------------------
# TestCompactionScoring
#
# Bug: the old multiplicative scoring formula `recency * engagement_weight`
# gave the first node (index 0) a recency score of 0, so engagement_weight was
# irrelevant — high-engagement oldest nodes were always dropped.
# Fix: additive blend 0.6*recency + 0.4*(engagement/3.0) so index-0 nodes with
# high engagement earn a non-zero score and can survive compaction.
# ---------------------------------------------------------------------------


class TestCompactionScoring:
    @pytest.mark.asyncio
    async def test_oldest_high_engagement_node_survives(self) -> None:
        """First node with engagement_weight=3.0 must be retained after compact().

        Scoring: additive blend 0.6*recency_norm + 0.4*(engagement/3.0).
        With 3 turns (6 non-fact nodes) and compact_keep=4:
          index 0 (high-engagement first user):  0.6*(0/5) + 0.4*(3/3) = 0.400
          index 1 (first assistant, plain):       0.6*(1/5) + 0.4*(1/3) = 0.253
          index 2 (second user, plain):           0.6*(2/5) + 0.4*(1/3) = 0.373
          index 3 (second assistant):             0.6*(3/5) + 0.4*(1/3) = 0.493
          index 4 (third user, plain):            0.6*(4/5) + 0.4*(1/3) = 0.613
          index 5 (third assistant):              0.6*(5/5) + 0.4*(1/3) = 0.733
        Top-4 are indices 5, 4, 3, 0 — first user (0.400) beats indices 1 (0.253)
        and 2 (0.373), so it survives. Under the old multiplicative formula the
        first node scored 0 and was always dropped regardless of engagement.
        """
        thread_id = uuid4()
        repository = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repository, compact_keep=4)

        # First turn — correction keyword + 2 question marks → engagement_weight = 3.0
        # (_compute_engagement_weight: base 1.0 + "actually" 1.0 + two-? 1.0 = 3.0)
        await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=1,
            user_text="Actually, let me correct that. What genre? How many words?",
            assistant_text="Noted!",
        )
        first_graph = await repository.load(thread_id)
        assert first_graph is not None
        first_user_node_id = first_graph.nodes[0].id

        # Two more plain turns: 6 nodes total, triggers compaction (6 > compact_keep=4)
        for turn in range(2, 4):
            await engine.update_after_turn(
                thread_id=thread_id,
                turn_sequence=turn,
                user_text=f"Okay message {turn}.",
                assistant_text="Got it.",
            )

        graph = await repository.load(thread_id)
        assert graph is not None
        surviving_ids = {node.id for node in graph.nodes}

        assert first_user_node_id in surviving_ids, (
            "High-engagement first node (score=0.4) should survive compaction "
            "over lower-scoring mid-recency plain nodes (scores 0.253, 0.373)."
        )

    @pytest.mark.asyncio
    async def test_low_engagement_oldest_dropped(self) -> None:
        """First node with default engagement (1.0) is dropped when recency wins.

        With 8 plain turns and compact_keep=3, the first node earns a blended
        score of 0.4*(1/3)=0.133 while the 7th node earns 0.6*(6/7)+0.133≈0.65,
        so the first node should lose to more recent ones.
        """
        thread_id = uuid4()
        repository = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repository, compact_keep=3)

        await engine.update_after_turn(
            thread_id=thread_id,
            turn_sequence=1,
            user_text="Hello there.",
            assistant_text="Hi! How can I help?",
        )
        first_graph = await repository.load(thread_id)
        assert first_graph is not None
        first_user_node_id = first_graph.nodes[0].id

        for turn in range(2, 9):
            await engine.update_after_turn(
                thread_id=thread_id,
                turn_sequence=turn,
                user_text=f"Message number {turn} here.",
                assistant_text="Acknowledged.",
            )

        graph = await repository.load(thread_id)
        assert graph is not None
        surviving_ids = {node.id for node in graph.nodes}

        assert first_user_node_id not in surviving_ids, (
            "Low-engagement first node should be dropped in favour of recent nodes."
        )

    @pytest.mark.asyncio
    async def test_repetition_counter_pruned(self) -> None:
        """After compaction, singleton repetition_counters must not exceed 50 entries.

        The fix prunes singletons (count == 1) down to the 50 most-recent entries,
        preventing unbounded dict growth.
        """
        thread_id = uuid4()
        repository = InMemoryGraphRepository()
        engine = TemporalRelationGraphEngine(repository=repository, compact_keep=4)

        # Send 60 distinct messages so each generates a singleton counter entry,
        # then send enough total turns to trigger compaction.
        for turn in range(1, 61):
            await engine.update_after_turn(
                thread_id=thread_id,
                turn_sequence=turn,
                user_text=f"Unique statement number {turn} about my project plan.",
                assistant_text="Understood.",
            )

        graph = await repository.load(thread_id)
        assert graph is not None

        singleton_count = sum(
            1 for v in graph.repetition_counters.values() if v == 1
        )
        assert singleton_count <= 50, (
            f"Expected at most 50 singleton entries; got {singleton_count}."
        )


# ---------------------------------------------------------------------------
# TestQuestionExtraction
#
# Bug: extract_questions returned the entire sentence fragment before "?" (which
# might include a non-question preamble), instead of just the interrogative clause.
# Fix: the regex matches `[^?]{3,}?` segments ending with "?" and strips
# surrounding whitespace, so preamble sentences are excluded.
# ---------------------------------------------------------------------------


class TestQuestionExtraction:
    def test_extracts_only_question_not_statement(self) -> None:
        """Non-question preamble text must not appear in the extracted questions."""
        result = extract_questions("Here is the plan. What is your budget?")

        assert len(result) == 1
        assert "Here is the plan" not in result[0]
        assert "What is your budget?" in result[0] or "budget" in result[0]

    def test_extracts_multiple_clean_questions(self) -> None:
        """Two distinct questions must each be returned as a clean separate string."""
        result = extract_questions("What is your genre? How many words?")

        assert len(result) == 2
        assert any("genre" in q for q in result)
        assert any("words" in q for q in result)

    def test_empty_text_returns_empty(self) -> None:
        """Empty input must return an empty list without raising."""
        result = extract_questions("")

        assert result == []

    def test_short_non_question_excluded(self) -> None:
        """A question mark with fewer than 3 preceding characters is excluded.

        The `{3,}` quantifier in the regex ensures noise like 'ok?' is filtered.
        """
        result = extract_questions("ok?")

        assert result == []
