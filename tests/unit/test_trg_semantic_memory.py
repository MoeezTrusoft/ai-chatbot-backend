from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.components.trg.engine import (
    TemporalRelationGraphEngine,
    _forbidden_reasks_from_facts,
    _update_semantic_facts,
    _update_service_shifts,
)
from bookcraft.components.trg.schemas import (
    TemporalRelationGraph,
    TRGFactNode,
)
from bookcraft.domain.enums import Source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delta(
    path: str,
    value: str,
    confidence: float = 0.9,
    raw_excerpt: str | None = None,
) -> StateDelta:
    return StateDelta(
        path=path,
        value=value,
        confidence=confidence,
        source=Source.USER_STATED,
        extracted_by="test",
        raw_excerpt=raw_excerpt,
    )


def _graph() -> TemporalRelationGraph:
    return TemporalRelationGraph(thread_id=uuid4())


def _engine() -> TemporalRelationGraphEngine:
    return TemporalRelationGraphEngine()


# ---------------------------------------------------------------------------
# _update_semantic_facts
# ---------------------------------------------------------------------------


def test_semantic_fact_created_from_state_delta() -> None:
    graph = _graph()
    _update_semantic_facts(graph, [_delta("project.genre", "children's fiction")], turn_id="t1")
    assert len(graph.semantic_facts) == 1
    fact = graph.semantic_facts[0]
    assert fact.fact_path == "project.genre"
    assert fact.value == "children's fiction"
    assert fact.active is True
    assert fact.source_turn_id == "t1"


def test_multiple_deltas_create_multiple_facts() -> None:
    graph = _graph()
    _update_semantic_facts(
        graph,
        [
            _delta("project.genre", "fantasy"),
            _delta("project.manuscript_status", "completed_draft"),
        ],
        turn_id="t1",
    )
    assert len(graph.semantic_facts) == 2
    paths = {f.fact_path for f in graph.semantic_facts}
    assert "project.genre" in paths
    assert "project.manuscript_status" in paths


def test_updating_same_fact_supersedes_old_one() -> None:
    graph = _graph()
    _update_semantic_facts(graph, [_delta("project.genre", "fantasy")], turn_id="t1")
    _update_semantic_facts(graph, [_delta("project.genre", "romance")], turn_id="t2")

    active = [f for f in graph.semantic_facts if f.active]
    inactive = [f for f in graph.semantic_facts if not f.active]

    assert len(active) == 1
    assert active[0].value == "romance"
    assert len(inactive) == 1
    assert inactive[0].value == "fantasy"
    assert inactive[0].superseded_by == "t2"


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------


def test_contradiction_event_created_when_value_changes() -> None:
    graph = _graph()
    _update_semantic_facts(graph, [_delta("project.genre", "fantasy")], turn_id="t1")
    _update_semantic_facts(graph, [_delta("project.genre", "romance")], turn_id="t2")

    assert len(graph.contradiction_events) == 1
    evt = graph.contradiction_events[0]
    assert evt.fact_path == "project.genre"
    assert evt.old_value == "fantasy"
    assert evt.new_value == "romance"
    assert evt.source_turn_id == "t2"
    assert evt.resolution_status == "unresolved"


def test_no_contradiction_when_value_unchanged() -> None:
    graph = _graph()
    _update_semantic_facts(graph, [_delta("project.genre", "fantasy")], turn_id="t1")
    _update_semantic_facts(graph, [_delta("project.genre", "fantasy")], turn_id="t2")
    assert len(graph.contradiction_events) == 0


def test_contradiction_is_case_insensitive() -> None:
    graph = _graph()
    _update_semantic_facts(graph, [_delta("project.genre", "Fantasy")], turn_id="t1")
    _update_semantic_facts(graph, [_delta("project.genre", "fantasy")], turn_id="t2")
    assert len(graph.contradiction_events) == 0


# ---------------------------------------------------------------------------
# Forbidden re-asks from facts
# ---------------------------------------------------------------------------


def test_forbidden_reasks_genre_known() -> None:
    facts = [TRGFactNode(fact_path="project.genre", value="children's fiction")]
    forbidden = _forbidden_reasks_from_facts(facts)
    assert "genre" in forbidden
    assert "what genre" in forbidden


def test_forbidden_reasks_manuscript_status_known() -> None:
    facts = [TRGFactNode(fact_path="project.manuscript_status", value="completed_draft")]
    forbidden = _forbidden_reasks_from_facts(facts)
    assert "manuscript_stage" in forbidden
    assert "draft status" in forbidden
    assert "starting from scratch" in forbidden


def test_forbidden_reasks_empty_when_no_facts() -> None:
    assert _forbidden_reasks_from_facts([]) == []


def test_forbidden_reasks_deduped() -> None:
    facts = [
        TRGFactNode(fact_path="project.genre", value="fantasy"),
        TRGFactNode(fact_path="project.genre", value="fantasy", active=False),
    ]
    forbidden = _forbidden_reasks_from_facts([f for f in facts if f.active])
    assert forbidden.count("genre") == 1


# ---------------------------------------------------------------------------
# Service shift tracking
# ---------------------------------------------------------------------------


def test_service_inertia_shift_recorded() -> None:
    graph = _graph()
    _update_service_shifts(
        graph, ["state_service_inertia:ghostwriting→cover_design_illustration"], turn_id="t1"
    )
    assert len(graph.service_shifts) == 1
    shift = graph.service_shifts[0]
    assert shift.mode == "inertia"
    assert shift.previous_service == "ghostwriting"
    assert shift.new_service == "cover_design_illustration"
    assert shift.source_turn_id == "t1"


def test_explicit_service_switch_recorded() -> None:
    graph = _graph()
    _update_service_shifts(graph, ["explicit_service_switch"], turn_id="t2")
    assert len(graph.service_shifts) == 1
    assert graph.service_shifts[0].mode == "switch"


def test_additive_service_recorded() -> None:
    graph = _graph()
    _update_service_shifts(graph, ["additive_service:marketing_promotion→secondary"], turn_id="t3")
    assert len(graph.service_shifts) == 1
    shift = graph.service_shifts[0]
    assert shift.mode == "addition"
    assert shift.new_service == "marketing_promotion"


def test_unrelated_signals_do_not_create_shifts() -> None:
    graph = _graph()
    _update_service_shifts(
        graph,
        ["pricing_negation_veto", "planner:primary_goal:cover_design_scoping"],
        turn_id="t1",
    )
    assert len(graph.service_shifts) == 0


# ---------------------------------------------------------------------------
# build_context — semantic fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_exposes_active_facts() -> None:
    engine = _engine()
    thread_id = uuid4()
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="My genre is children's fiction.",
        assistant_text="Got it. What is the manuscript stage?",
        state_deltas=[_delta("project.genre", "children's fiction")],
    )
    graph = await engine.repository.load(thread_id)
    assert graph is not None
    ctx = engine.build_context(graph)

    assert any(f.fact_path == "project.genre" for f in ctx.active_facts)
    assert "genre" in ctx.forbidden_reasks
    assert "what genre" in ctx.forbidden_reasks


@pytest.mark.asyncio
async def test_build_context_records_contradiction() -> None:
    engine = _engine()
    thread_id = uuid4()
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="My genre is fantasy.",
        assistant_text="Great.",
        state_deltas=[_delta("project.genre", "fantasy")],
    )
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=2,
        user_text="Actually it's romance.",
        assistant_text="Noted.",
        state_deltas=[_delta("project.genre", "romance")],
    )
    graph = await engine.repository.load(thread_id)
    assert graph is not None
    ctx = engine.build_context(graph)

    assert len(ctx.contradictions) == 1
    assert ctx.contradictions[0].fact_path == "project.genre"


@pytest.mark.asyncio
async def test_build_context_records_service_shift_via_arbiter_signals() -> None:
    engine = _engine()
    thread_id = uuid4()
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="I need cover design.",
        assistant_text="Sure. What genre?",
        arbiter_signals=["state_service_inertia:ghostwriting→cover_design_illustration"],
    )
    graph = await engine.repository.load(thread_id)
    assert graph is not None
    ctx = engine.build_context(graph)

    assert len(ctx.service_shifts) == 1
    assert ctx.service_shifts[0].mode == "inertia"


# ===========================================================================
# Required tests (exact names from spec)
# ===========================================================================


def test_trg_records_active_fact_from_state_delta() -> None:
    """State delta project.genre should create an active TRGFactNode."""
    graph = _graph()
    _update_semantic_facts(
        graph,
        [_delta("project.genre", "children's fiction")],
        turn_id="t1",
    )
    active = [f for f in graph.semantic_facts if f.active]
    assert any(f.fact_path == "project.genre" for f in active)
    fact = next(f for f in active if f.fact_path == "project.genre")
    assert fact.value == "children's fiction"
    assert fact.active is True


def test_trg_records_manuscript_status_fact() -> None:
    """State delta project.manuscript_status should create an active TRGFactNode."""
    graph = _graph()
    _update_semantic_facts(
        graph,
        [_delta("project.manuscript_status", "completed_draft")],
        turn_id="t1",
    )
    active = [f for f in graph.semantic_facts if f.active]
    assert any(f.fact_path == "project.manuscript_status" for f in active)
    fact = next(f for f in active if f.fact_path == "project.manuscript_status")
    assert fact.value == "completed_draft"


def test_trg_forbidden_reasks_from_known_genre() -> None:
    """Active fact project.genre must produce forbidden_reasks for genre and what genre."""
    facts = [TRGFactNode(fact_path="project.genre", value="children's fiction")]
    forbidden = _forbidden_reasks_from_facts(facts)
    assert "genre" in forbidden
    assert "what genre" in forbidden


def test_trg_forbidden_reasks_from_known_manuscript_status() -> None:
    """Active fact project.manuscript_status must produce manuscript_stage and draft_status."""
    facts = [TRGFactNode(fact_path="project.manuscript_status", value="completed_draft")]
    forbidden = _forbidden_reasks_from_facts(facts)
    assert "manuscript_stage" in forbidden
    assert any("draft" in label for label in forbidden), (
        f"Expected a draft-related label; got: {forbidden}"
    )


@pytest.mark.asyncio
async def test_trg_records_answered_question() -> None:
    """
    When assistant asks 'Do you have a draft?' and user answers
    'I have finished my manuscript.', the answered_questions list must
    contain a resolved AnsweredQuestion with fact_path project.manuscript_status.
    """
    engine = _engine()
    thread_id = uuid4()

    # Turn 1: assistant asks about draft status.
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="I need cover design.",
        assistant_text="Do you have a draft?",
    )

    # Turn 2: user answers; state delta carries manuscript_status.
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=2,
        user_text="I have finished my manuscript.",
        assistant_text="Great — finished manuscripts are ideal for cover design.",
        state_deltas=[_delta("project.manuscript_status", "completed_draft")],
    )

    graph = await engine.repository.load(thread_id)
    assert graph is not None
    assert graph.answered_questions, "Expected at least one answered question"
    aq = graph.answered_questions[0]
    assert aq.resolved is True
    assert "manuscript" in aq.answer_text.lower()
    # fact_path should be inferred from the state delta in the same turn.
    assert aq.fact_path == "project.manuscript_status"


@pytest.mark.asyncio
async def test_trg_records_contradiction() -> None:
    """
    When project.manuscript_status changes from completed_draft to idea_only,
    a ContradictionEvent must be recorded and the old fact must be superseded.
    """
    engine = _engine()
    thread_id = uuid4()

    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="I have a finished manuscript.",
        assistant_text="Got it.",
        state_deltas=[_delta("project.manuscript_status", "completed_draft")],
    )
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=2,
        user_text="Actually I only have an idea.",
        assistant_text="Understood.",
        state_deltas=[_delta("project.manuscript_status", "idea_only")],
    )

    graph = await engine.repository.load(thread_id)
    assert graph is not None

    # Old fact should be superseded.
    old_facts = [
        f
        for f in graph.semantic_facts
        if f.fact_path == "project.manuscript_status" and not f.active
    ]
    assert old_facts, "Expected old fact to be superseded"
    assert old_facts[0].value == "completed_draft"

    # Contradiction event should be recorded.
    assert graph.contradiction_events, "Expected a ContradictionEvent"
    evt = graph.contradiction_events[0]
    assert evt.fact_path == "project.manuscript_status"
    assert evt.old_value == "completed_draft"
    assert evt.new_value == "idea_only"
    assert evt.resolution_status == "unresolved"


@pytest.mark.asyncio
async def test_trg_records_service_shift() -> None:
    """
    When the arbiter signals an explicit service switch (cover design → editing),
    a ServiceShiftEvent with mode='switch' must be recorded.
    """
    engine = _engine()
    thread_id = uuid4()

    # Turn 1: cover design established (inertia signal).
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="I need cover design.",
        assistant_text="What cover style works?",
        arbiter_signals=["state_service_inertia:ghostwriting→cover_design_illustration"],
    )

    # Turn 2: explicit switch to editing.
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=2,
        user_text="Actually I need editing instead.",
        assistant_text="Editing it is.",
        arbiter_signals=["explicit_service_switch"],
    )

    graph = await engine.repository.load(thread_id)
    assert graph is not None

    switch_events = [s for s in graph.service_shifts if s.mode == "switch"]
    assert switch_events, f"Expected a switch ServiceShiftEvent; got: {graph.service_shifts}"


@pytest.mark.asyncio
async def test_trg_context_exposes_semantic_fields() -> None:
    """
    build_context must expose all five semantic fields:
    active_facts, forbidden_reasks, contradictions, answered_questions, service_shifts.
    """
    engine = _engine()
    thread_id = uuid4()

    # Set up facts, contradictions, answered questions, and service shifts.
    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=1,
        user_text="My genre is fantasy and I have a finished manuscript.",
        assistant_text="Got it. What cover style do you prefer?",
        state_deltas=[
            _delta("project.genre", "fantasy"),
            _delta("project.manuscript_status", "completed_draft"),
        ],
        arbiter_signals=["state_service_inertia:ghostwriting→cover_design_illustration"],
    )

    await engine.update_after_turn(
        thread_id=thread_id,
        turn_sequence=2,
        user_text="Actually the genre is romance.",
        assistant_text="Updated.",
        state_deltas=[_delta("project.genre", "romance")],
    )

    graph = await engine.repository.load(thread_id)
    assert graph is not None
    ctx = engine.build_context(graph)

    # active_facts — must contain genre (romance) and manuscript_status.
    active_paths = {f.fact_path for f in ctx.active_facts}
    assert "project.genre" in active_paths
    assert "project.manuscript_status" in active_paths

    # forbidden_reasks — genre + manuscript_status both known.
    assert "genre" in ctx.forbidden_reasks
    assert "manuscript_stage" in ctx.forbidden_reasks

    # contradictions — genre changed fantasy→romance.
    assert ctx.contradictions, "Expected at least one contradiction"
    assert ctx.contradictions[0].fact_path == "project.genre"

    # answered_questions — cover-style question was answered on turn 2.
    assert isinstance(ctx.answered_questions, list)

    # service_shifts — inertia shift recorded.
    assert ctx.service_shifts
    assert any(s.mode == "inertia" for s in ctx.service_shifts)
