from __future__ import annotations

from bookcraft.components.context.project_manager import (
    ProjectContext,
    ProjectContextManager,
)
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, ServiceCategory
from bookcraft.domain.state import ThreadState


def _manager() -> ProjectContextManager:
    return ProjectContextManager()


def _intent(service: ServiceCategory | None = None) -> IntentVote:
    from bookcraft.domain.enums import SalesStage

    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=service,
        funnel_stage=SalesStage.EXPLORING,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
    )


def _state_with_project(project: ProjectContext | None = None) -> ThreadState:
    state = ThreadState()
    if project is not None:
        state.conversation_projects = [project.model_dump(mode="json")]
    return state


def _state_with_two_projects(
    p1: ProjectContext,
    p2: ProjectContext,
) -> ThreadState:
    state = ThreadState()
    state.conversation_projects = [
        p1.model_dump(mode="json"),
        p2.model_dump(mode="json"),
    ]
    return state


# ---------------------------------------------------------------------------
# 1. New book marker creates new project
# ---------------------------------------------------------------------------


def test_new_book_creates_new_project_decision() -> None:
    existing = ProjectContext(
        project_id="proj-aaa",
        active=True,
        service_focus=["cover_design_illustration"],
    )
    state = _state_with_project(existing)
    result = _manager().decide(
        message="I have a new book that needs ghostwriting.",
        state=state,
        intent=_intent(ServiceCategory.GHOSTWRITING),
    )
    assert result.decision.event == "new_project"
    assert result.active_project_id != "proj-aaa"
    assert result.previous_project_id == "proj-aaa"
    assert result.decision.carry_over_allowed is False


# ---------------------------------------------------------------------------
# 2. "second book" marker creates new project
# ---------------------------------------------------------------------------


def test_second_book_creates_new_project_decision() -> None:
    existing = ProjectContext(project_id="proj-bbb", active=True)
    state = _state_with_project(existing)
    result = _manager().decide(
        message="I also want to work on a second book for editing.",
        state=state,
        intent=_intent(ServiceCategory.EDITING_PROOFREADING),
    )
    assert result.decision.event == "new_project"
    assert result.previous_project_id == "proj-bbb"


# ---------------------------------------------------------------------------
# 3. Same-project service addition does not reset active project
# ---------------------------------------------------------------------------


def test_same_project_service_addition_does_not_reset_project() -> None:
    existing = ProjectContext(
        project_id="proj-ccc",
        active=True,
        service_focus=["cover_design_illustration"],
    )
    state = _state_with_project(existing)
    result = _manager().decide(
        message="I also need interior formatting for the same book.",
        state=state,
        intent=_intent(ServiceCategory.INTERIOR_FORMATTING),
    )
    assert result.decision.event == "same_project_service_addition"
    assert result.active_project_id == "proj-ccc"
    assert result.previous_project_id is None
    assert result.decision.carry_over_allowed is True


# ---------------------------------------------------------------------------
# 4. Previous book marker triggers project switch
# ---------------------------------------------------------------------------


def test_previous_book_switch_decision() -> None:
    p1 = ProjectContext(project_id="proj-old", active=False)
    p2 = ProjectContext(project_id="proj-cur", active=True)
    state = _state_with_two_projects(p1, p2)
    result = _manager().decide(
        message="Let's go back to the previous book I mentioned.",
        state=state,
        intent=_intent(),
    )
    assert result.decision.event == "project_switch"
    assert result.active_project_id == "proj-old"
    assert result.previous_project_id == "proj-cur"


# ---------------------------------------------------------------------------
# 5. Ambiguous weak-marker + service → ambiguous_project_reference
# ---------------------------------------------------------------------------


def test_ambiguous_project_reference_requires_clarification() -> None:
    existing = ProjectContext(project_id="proj-ddd", active=True)
    state = _state_with_project(existing)
    result = _manager().decide(
        message="Now I need editing too.",
        state=state,
        intent=_intent(ServiceCategory.EDITING_PROOFREADING),
    )
    assert result.decision.event == "ambiguous_project_reference"
    assert result.decision.carry_over_allowed is False
    assert result.decision.confidence <= 0.6


# ---------------------------------------------------------------------------
# 6. Snapshot preserves previous (inactive) project in projects list
# ---------------------------------------------------------------------------


def test_project_context_snapshot_preserves_previous_project() -> None:
    existing = ProjectContext(
        project_id="proj-eee",
        active=True,
        service_focus=["ghostwriting"],
    )
    state = _state_with_project(existing)
    result = _manager().decide(
        message="I have another book that needs cover design.",
        state=state,
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )
    assert result.decision.event == "new_project"
    all_ids = {p.project_id for p in result.projects}
    assert "proj-eee" in all_ids, "Previous project must remain in snapshot"
    inactive = [p for p in result.projects if p.project_id == "proj-eee"]
    assert inactive and not inactive[0].active, "Previous project must be deactivated"


# ---------------------------------------------------------------------------
# 7. New project starts with empty known_facts
# ---------------------------------------------------------------------------


def test_new_project_does_not_carry_over_facts() -> None:
    existing = ProjectContext(
        project_id="proj-fff",
        active=True,
        known_facts={"genre": "fantasy", "word_count": 50000},
    )
    state = _state_with_project(existing)
    result = _manager().decide(
        message="I have a different book for a children's series.",
        state=state,
        intent=_intent(ServiceCategory.GHOSTWRITING),
    )
    assert result.decision.event == "new_project"
    new_proj = next(p for p in result.projects if p.project_id == result.active_project_id)
    # New project has no inherited facts.
    assert new_proj.known_facts == {}


# ---------------------------------------------------------------------------
# 8. Same-project bundle keeps and extends service_focus
# ---------------------------------------------------------------------------


def test_same_project_bundle_keeps_service_focus() -> None:
    existing = ProjectContext(
        project_id="proj-ggg",
        active=True,
        service_focus=["cover_design_illustration"],
    )
    state = _state_with_project(existing)
    result = _manager().decide(
        message="I also need interior formatting and KDP publishing for the same book.",
        state=state,
        intent=_intent(ServiceCategory.INTERIOR_FORMATTING),
    )
    assert result.decision.event == "same_project_service_addition"
    active = next(p for p in result.projects if p.project_id == result.active_project_id)
    assert "cover_design_illustration" in active.service_focus
    assert "interior_formatting" in active.service_focus


# ---------------------------------------------------------------------------
# 9. First-turn (no prior projects) creates a project silently
# ---------------------------------------------------------------------------


def test_first_turn_creates_default_project() -> None:
    state = ThreadState()
    result = _manager().decide(
        message="I need help with ghostwriting.",
        state=state,
        intent=_intent(ServiceCategory.GHOSTWRITING),
    )
    assert result.decision.event == "same_project"
    assert result.active_project_id is not None
    assert len(result.projects) == 1
    assert result.projects[0].active is True
    assert "ghostwriting" in result.projects[0].service_focus


# ---------------------------------------------------------------------------
# 10. Previous-book marker with only one project falls back to same_project
# ---------------------------------------------------------------------------


def test_previous_book_with_single_project_is_same_project() -> None:
    existing = ProjectContext(project_id="proj-hhh", active=True)
    state = _state_with_project(existing)
    result = _manager().decide(
        message="Actually, let me go back to the first book.",
        state=state,
        intent=_intent(),
    )
    # No inactive project exists, so treated as same project.
    assert result.decision.event == "same_project"
    assert result.active_project_id == "proj-hhh"
