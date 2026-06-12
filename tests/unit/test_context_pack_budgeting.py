"""Tests for context pack token budgeting — _trim_facts_by_priority and _MAX_TOTAL_FACTS."""
from __future__ import annotations

import pytest

from bookcraft.components.context.pack_builder import (
    ContextPackBuilder,
    _MAX_TOTAL_FACTS,
    _trim_facts_by_priority,
)
from bookcraft.components.context.schemas import KnownFact
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


def _make_known_fact(path: str, value: str = "test") -> KnownFact:
    return KnownFact(
        path=path,
        label=path.split(".")[-1],
        value=value,
        confidence=0.9,
        source="test",
        raw_excerpt=None,
    )


class TestTrimFactsByPriority:
    def test_below_max_unchanged(self):
        facts = [_make_known_fact(f"project.field_{i}") for i in range(5)]
        result = _trim_facts_by_priority(facts, 30)
        assert len(result) == 5

    def test_exactly_at_max_unchanged(self):
        # Use facts spread across tiers so the result is not capped by any single tier cap.
        # contact cap=10, project cap=15, service cap=10 → enough for _MAX_TOTAL_FACTS=30
        facts = (
            [_make_known_fact(f"contact.field_{i}") for i in range(5)]
            + [_make_known_fact(f"project.field_{i}") for i in range(15)]
            + [_make_known_fact(f"service.field_{i}") for i in range(10)]
        )
        assert len(facts) == 30
        result = _trim_facts_by_priority(facts, _MAX_TOTAL_FACTS)
        assert len(result) == _MAX_TOTAL_FACTS

    def test_trims_to_max_count(self):
        facts = [_make_known_fact(f"other.field_{i}") for i in range(50)]
        result = _trim_facts_by_priority(facts, 10)
        assert len(result) <= 10

    def test_contact_facts_kept_over_other(self):
        # Mix contact (high priority) with lots of other (low priority)
        facts = [_make_known_fact("contact.name"), _make_known_fact("contact.email")]
        facts += [_make_known_fact(f"other.field_{i}") for i in range(20)]
        result = _trim_facts_by_priority(facts, 5)
        result_paths = {f.path for f in result}
        assert "contact.name" in result_paths
        assert "contact.email" in result_paths

    def test_project_facts_kept_over_other(self):
        facts = [_make_known_fact("project.genre"), _make_known_fact("project.word_count")]
        facts += [_make_known_fact(f"other.field_{i}") for i in range(20)]
        result = _trim_facts_by_priority(facts, 3)
        result_paths = {f.path for f in result}
        assert "project.genre" in result_paths

    def test_personal_treated_as_contact_tier(self):
        """personal.* paths should be treated as contact tier (high priority)."""
        facts = [_make_known_fact("personal.name"), _make_known_fact("personal.email")]
        facts += [_make_known_fact(f"other.field_{i}") for i in range(20)]
        result = _trim_facts_by_priority(facts, 3)
        result_paths = {f.path for f in result}
        # personal.* should survive since they map to contact tier
        assert "personal.name" in result_paths

    def test_empty_facts_returns_empty(self):
        result = _trim_facts_by_priority([], 10)
        assert result == []

    def test_result_is_list_of_known_facts(self):
        facts = [_make_known_fact("project.genre")]
        result = _trim_facts_by_priority(facts, 5)
        assert isinstance(result, list)
        assert all(isinstance(f, KnownFact) for f in result)

    def test_max_count_one_keeps_highest_priority(self):
        """With max=1, we should keep a contact fact over an 'other' fact."""
        facts = [
            _make_known_fact("other.something"),
            _make_known_fact("contact.name"),
        ]
        result = _trim_facts_by_priority(facts, 1)
        assert len(result) == 1
        # contact comes first in priority
        assert result[0].path == "contact.name"


class TestContextPackBuilder:
    def _make_intent(self) -> IntentVote:
        return IntentVote(
            query_primary=QueryIntentType.SERVICE_QUESTION,
            service_primary=None,
            funnel_stage=SalesStage.SERVICE_DISCOVERY,
            needs_clarification=False,
            confidence=0.9,
            rationale="test",
            evidence=[],
        )

    def test_build_returns_context_pack(self):
        builder = ContextPackBuilder()
        state = ThreadState()
        pack = builder.build(state=state, intent=self._make_intent())
        assert pack is not None

    def test_known_facts_capped_at_max(self):
        # Verify the constant is sane
        assert _MAX_TOTAL_FACTS > 0
        assert _MAX_TOTAL_FACTS <= 100

    def test_max_total_facts_constant_value(self):
        # The implementation sets this to 30
        assert _MAX_TOTAL_FACTS == 30

    def test_build_with_empty_state_has_no_facts(self):
        builder = ContextPackBuilder()
        state = ThreadState()
        pack = builder.build(state=state, intent=self._make_intent())
        # Empty state → no project/contact facts
        assert len(pack.known_facts) == 0
