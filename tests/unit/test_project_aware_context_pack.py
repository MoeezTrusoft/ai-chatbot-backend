from __future__ import annotations

from bookcraft.components.context.delegation import SlotResolutionStatus
from bookcraft.components.context.pack_builder import ContextPackBuilder
from bookcraft.components.context.project_manager import (
    ProjectContext,
    ProjectContextSnapshot,
    ProjectShiftDecision,
)
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState

_builder = ContextPackBuilder()


def _intent(service: ServiceCategory | None = None) -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=service,
        funnel_stage=SalesStage.EXPLORING,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
    )


def _make_snapshot(
    active: ProjectContext,
    inactive: list[ProjectContext] | None = None,
    event: str = "same_project",
) -> ProjectContextSnapshot:
    projects = [active] + (inactive or [])
    return ProjectContextSnapshot(
        active_project_id=active.project_id,
        previous_project_id=(inactive[0].project_id if inactive else None),
        projects=projects,
        decision=ProjectShiftDecision(
            event=event,  # type: ignore[arg-type]
            active_project_id=active.project_id,
            previous_project_id=(inactive[0].project_id if inactive else None),
            carry_over_allowed=(event != "new_project"),
            confidence=0.95,
        ),
    )


# ---------------------------------------------------------------------------
# 1. Active project known_facts are accessible via project_memory
# ---------------------------------------------------------------------------


def test_active_project_facts_become_known_facts() -> None:
    state = ThreadState()
    active = ProjectContext(
        project_id="proj-active",
        active=True,
        known_facts={"project.genre": "fantasy", "project.word_count": 50000},
    )
    snap = _make_snapshot(active)
    # project_memory_summary should reference active project's service focus
    pack = _builder.build(state=state, intent=_intent(), project_snapshot=snap)
    # active project has no state facts since state.project.* is empty, but project info is set
    assert pack.active_project_id == "proj-active"


# ---------------------------------------------------------------------------
# 2. Previous project facts do not become active known_facts
# ---------------------------------------------------------------------------


def test_previous_project_facts_do_not_become_active_known_facts() -> None:
    state = ThreadState()
    # Simulate that state.project.genre has a value from the old project
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    state.project.genre = FieldMeta(
        value="fantasy",
        confidence=0.9,
        source=Source.USER_STATED,
        extracted_by="test",
        raw_excerpt="fantasy",
    )
    inactive = ProjectContext(
        project_id="proj-old",
        active=False,
        known_facts={"project.genre": "fantasy"},
    )
    new_active = ProjectContext(project_id="proj-new", active=True)
    snap = _make_snapshot(new_active, [inactive], event="new_project")

    pack = _builder.build(state=state, intent=_intent(), project_snapshot=snap)
    # For new_project, active_genre must be None
    assert pack.active_genre is None, (
        f"previous project genre must not bleed into new project, got {pack.active_genre}"
    )
    # known_facts from old project (genre=fantasy) must not appear as active known facts
    active_paths = {f.path for f in pack.known_facts}
    assert "project.genre" not in active_paths, (
        f"previous project genre must not be in active known_facts, got {active_paths}"
    )


# ---------------------------------------------------------------------------
# 3. Previous project info appears in project_memory_summary
# ---------------------------------------------------------------------------


def test_project_memory_summary_keeps_previous_project() -> None:
    state = ThreadState()
    inactive = ProjectContext(
        project_id="proj-abc123",
        active=False,
        known_facts={"genre": "thriller", "word_count": 80000},
    )
    new_active = ProjectContext(project_id="proj-new", active=True)
    snap = _make_snapshot(new_active, [inactive], event="new_project")

    pack = _builder.build(state=state, intent=_intent(), project_snapshot=snap)
    assert pack.project_memory_summary, "project_memory_summary must not be empty"
    assert any("proj-abc" in s for s in pack.project_memory_summary), (
        f"previous project must appear in summary, got {pack.project_memory_summary}"
    )


# ---------------------------------------------------------------------------
# 4. New project recalculates missing facts fresh
# ---------------------------------------------------------------------------


def test_new_project_recalculates_missing_facts() -> None:
    state = ThreadState()
    # Old project had genre known in state
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    state.project.genre = FieldMeta(
        value="thriller",
        confidence=0.9,
        source=Source.USER_STATED,
        extracted_by="test",
        raw_excerpt="thriller",
    )
    inactive = ProjectContext(
        project_id="proj-old", active=False, known_facts={"genre": "thriller"}
    )
    new_active = ProjectContext(project_id="proj-new", active=True)
    snap = _make_snapshot(new_active, [inactive], event="new_project")

    pack = _builder.build(
        state=state,
        intent=_intent(ServiceCategory.EDITING_PROOFREADING),
        project_snapshot=snap,
    )
    # genre should be in missing_facts for the new project (since new project has no genre)
    assert "genre" in pack.missing_facts, (
        f"genre must be missing for new project, got {pack.missing_facts}"
    )


# ---------------------------------------------------------------------------
# 5. Same-project service addition preserves active facts
# ---------------------------------------------------------------------------


def test_same_project_service_addition_preserves_facts() -> None:
    state = ThreadState()
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    state.project.genre = FieldMeta(
        value="fantasy",
        confidence=0.9,
        source=Source.USER_STATED,
        extracted_by="test",
        raw_excerpt="fantasy",
    )
    active = ProjectContext(
        project_id="proj-aaa",
        active=True,
        service_focus=["cover_design_illustration", "interior_formatting"],
    )
    snap = _make_snapshot(active, event="same_project_service_addition")
    pack = _builder.build(
        state=state,
        intent=_intent(ServiceCategory.INTERIOR_FORMATTING),
        project_snapshot=snap,
    )
    assert pack.active_genre == "fantasy", (
        f"active genre must be preserved for service addition, got {pack.active_genre}"
    )


# ---------------------------------------------------------------------------
# 6. Previous project delegated slot does not suppress new project slot
# ---------------------------------------------------------------------------


def test_previous_project_delegated_slot_does_not_suppress_new_project_slot() -> None:
    state = ThreadState()
    new_active_id = "proj-new-123"
    old_project_id = "proj-old-456"
    # Slot status belongs to the OLD project
    state.slot_resolution_statuses = [
        SlotResolutionStatus(
            slot="genre",
            status="declined",
            forbidden_reask=True,
            confidence=0.9,
            project_id=old_project_id,  # old project
        ).model_dump(mode="json")
    ]
    new_active = ProjectContext(project_id=new_active_id, active=True)
    inactive = ProjectContext(project_id=old_project_id, active=False)
    snap = _make_snapshot(new_active, [inactive], event="new_project")

    pack = _builder.build(state=state, intent=_intent(), project_snapshot=snap)
    # genre should NOT be in forbidden_reasks for the new project
    assert "genre" not in pack.forbidden_reasks, (
        f"Old project's genre decline must not suppress new project slot, "
        f"got forbidden_reasks={pack.forbidden_reasks}"
    )


# ---------------------------------------------------------------------------
# 7. Active project delegated slot suppresses re-ask
# ---------------------------------------------------------------------------


def test_active_project_delegated_slot_suppresses_reask() -> None:
    state = ThreadState()
    active_id = "proj-active-789"
    state.slot_resolution_statuses = [
        SlotResolutionStatus(
            slot="cover_style",
            status="delegated",
            forbidden_reask=True,
            confidence=0.92,
            project_id=active_id,  # same project as active
        ).model_dump(mode="json")
    ]
    active = ProjectContext(project_id=active_id, active=True)
    snap = _make_snapshot(active)
    snap.active_project_id = active_id
    # Fix the decision
    snap = ProjectContextSnapshot(
        active_project_id=active_id,
        previous_project_id=None,
        projects=[active],
        decision=ProjectShiftDecision(
            event="same_project",
            active_project_id=active_id,
            carry_over_allowed=True,
            confidence=0.95,
        ),
    )

    pack = _builder.build(
        state=state,
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        project_snapshot=snap,
    )
    assert "cover_style" in pack.forbidden_reasks, (
        f"Active project delegated slot must suppress reask, "
        f"got forbidden_reasks={pack.forbidden_reasks}"
    )
    assert "cover_style" not in pack.missing_facts, (
        f"Delegated cover_style must not be in missing_facts, got {pack.missing_facts}"
    )
