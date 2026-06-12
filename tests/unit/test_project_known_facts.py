"""Tests for ProjectContext.known_facts population and _snapshot_state_facts."""
from __future__ import annotations

from uuid import uuid4

from bookcraft.components.context.project_manager import (
    ProjectContextManager,
    _snapshot_state_facts,
)
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


def _make_state(genre=None, word_count=None, name=None) -> ThreadState:
    state = ThreadState()
    if genre is not None:
        state.project.genre.value = genre
    if word_count is not None:
        state.project.word_count.value = word_count
    if name is not None:
        state.personal.name.value = name
    return state


def _make_intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=None,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )


class TestSnapshotStateFacts:
    def test_empty_state_returns_empty_dict(self):
        state = ThreadState()
        facts = _snapshot_state_facts(state)
        assert isinstance(facts, dict)
        assert len(facts) == 0

    def test_genre_captured(self):
        state = _make_state(genre="fantasy")
        facts = _snapshot_state_facts(state)
        assert "project.genre" in facts
        assert facts["project.genre"] == "fantasy"

    def test_word_count_captured(self):
        state = _make_state(word_count=80000)
        facts = _snapshot_state_facts(state)
        assert "project.word_count" in facts
        assert facts["project.word_count"] == 80000

    def test_name_captured(self):
        state = _make_state(name="Alice")
        facts = _snapshot_state_facts(state)
        assert "contact.name" in facts
        assert facts["contact.name"] == "Alice"

    def test_none_values_not_included(self):
        state = ThreadState()
        facts = _snapshot_state_facts(state)
        assert "project.genre" not in facts
        assert "project.word_count" not in facts

    def test_multiple_fields_captured(self):
        state = _make_state(genre="mystery", word_count=60000, name="Bob")
        facts = _snapshot_state_facts(state)
        assert "project.genre" in facts
        assert "project.word_count" in facts
        assert "contact.name" in facts

    def test_returns_dict_type(self):
        state = _make_state(genre="sci-fi")
        facts = _snapshot_state_facts(state)
        assert isinstance(facts, dict)


class TestKnownFactsOnFirstProject:
    def test_first_project_gets_known_facts(self):
        manager = ProjectContextManager()
        state = _make_state(genre="fantasy", word_count=80000)
        intent = _make_intent()
        snapshot = manager.decide(message="I need ghostwriting", state=state, intent=intent)

        active = next(p for p in snapshot.projects if p.active)
        assert "project.genre" in active.known_facts
        assert active.known_facts["project.genre"] == "fantasy"

    def test_first_project_empty_state_empty_facts(self):
        manager = ProjectContextManager()
        state = ThreadState()
        intent = _make_intent()
        snapshot = manager.decide(message="Hi there", state=state, intent=intent)

        active = next(p for p in snapshot.projects if p.active)
        assert isinstance(active.known_facts, dict)
        assert len(active.known_facts) == 0

    def test_first_project_word_count_in_facts(self):
        manager = ProjectContextManager()
        state = _make_state(word_count=50000)
        intent = _make_intent()
        snapshot = manager.decide(message="I need editing", state=state, intent=intent)

        active = next(p for p in snapshot.projects if p.active)
        assert "project.word_count" in active.known_facts
        assert active.known_facts["project.word_count"] == 50000


class TestKnownFactsOnNewProject:
    def test_outgoing_project_facts_snapshotted(self):
        manager = ProjectContextManager()
        state = _make_state(genre="fantasy")
        intent = _make_intent()

        # Seed state with an existing project by using conversation_projects
        # We need to pre-seed the manager so it has a prior project.
        # First, create a snapshot with a project active:
        snap1 = manager.decide(message="I need ghostwriting", state=state, intent=intent)

        # The first turn creates a project — now inject it into state.conversation_projects
        state2 = _make_state(genre="sci-fi")
        state2.conversation_projects = [p.model_dump() for p in snap1.projects]

        snap2 = manager.decide(message="Actually I have another book", state=state2, intent=intent)

        assert snap2.decision.event == "new_project"
        # The deactivated old project should have known_facts
        old_projects = [p for p in snap2.projects if not p.active]
        assert len(old_projects) >= 1
        old_project = old_projects[0]
        assert isinstance(old_project.known_facts, dict)

    def test_new_project_starts_empty_facts(self):
        """New project created via 'another book' has empty known_facts."""
        manager = ProjectContextManager()
        state = _make_state(genre="fantasy")
        intent = _make_intent()
        snap1 = manager.decide(message="I need ghostwriting", state=state, intent=intent)

        state2 = _make_state(genre="sci-fi")
        state2.conversation_projects = [p.model_dump() for p in snap1.projects]
        snap2 = manager.decide(message="I have another book", state=state2, intent=intent)

        new_project = next(p for p in snap2.projects if p.active)
        # New project starts with no inherited facts
        assert isinstance(new_project.known_facts, dict)
        assert len(new_project.known_facts) == 0


class TestProjectContextSnapshotProperty:
    def test_active_project_known_facts_property(self):
        manager = ProjectContextManager()
        state = _make_state(genre="mystery")
        intent = _make_intent()
        snapshot = manager.decide(message="I need editing", state=state, intent=intent)

        # The property should return the known_facts for the active project
        facts = snapshot.active_project_known_facts
        assert isinstance(facts, dict)

    def test_active_project_known_facts_returns_genre(self):
        manager = ProjectContextManager()
        state = _make_state(genre="romance")
        intent = _make_intent()
        snapshot = manager.decide(message="I need formatting", state=state, intent=intent)

        facts = snapshot.active_project_known_facts
        assert "project.genre" in facts
        assert facts["project.genre"] == "romance"
