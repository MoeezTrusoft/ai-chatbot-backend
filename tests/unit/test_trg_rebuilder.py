"""Tests for the TRG graph rebuilder (rebuilder.py)."""
from __future__ import annotations

import pytest
from uuid import uuid4

from bookcraft.components.trg import InMemoryGraphRepository, TemporalRelationGraphEngine
from bookcraft.components.trg.rebuilder import rebuild_graph, RebuildResult, _extract_turns


class TestExtractTurns:
    def test_empty_events_returns_empty(self):
        result = _extract_turns([])
        assert result == []

    def test_user_without_assistant_returns_empty(self):
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "Hello"}},
        ]
        result = _extract_turns(events)
        assert result == []  # no matching assistant.response yet

    def test_paired_turn_extracted(self):
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "I need ghostwriting"}},
            {"sequence": 2, "event_type": "assistant.response", "payload": {"preview": "I can help!"}},
        ]
        turns = _extract_turns(events)
        assert len(turns) == 1
        assert turns[0]["user_text"] == "I need ghostwriting"
        assert turns[0]["assistant_text"] == "I can help!"

    def test_two_turns_extracted(self):
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "First message"}},
            {"sequence": 2, "event_type": "assistant.response", "payload": {"preview": "First reply"}},
            {"sequence": 3, "event_type": "user.message", "payload": {"text": "Second message"}},
            {"sequence": 4, "event_type": "assistant.response", "payload": {"preview": "Second reply"}},
        ]
        turns = _extract_turns(events)
        assert len(turns) == 2

    def test_non_paired_events_ignored(self):
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "Hello"}},
            {"sequence": 2, "event_type": "trimatch.voted", "payload": {"service": "ghostwriting"}},
            {"sequence": 3, "event_type": "assistant.response", "payload": {"preview": "Hi!"}},
        ]
        turns = _extract_turns(events)
        assert len(turns) == 1

    def test_intent_classified_with_state_deltas_captured(self):
        """intent.classified events with state_deltas should be collected into the turn."""
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "I write fantasy"}},
            {
                "sequence": 2,
                "event_type": "intent.classified",
                "payload": {
                    "intent": {
                        "state_deltas": [{"path": "project.genre", "value": "fantasy"}],
                    }
                },
            },
            {"sequence": 3, "event_type": "assistant.response", "payload": {"preview": "Great!"}},
        ]
        turns = _extract_turns(events)
        assert len(turns) == 1
        assert len(turns[0]["state_deltas"]) == 1

    def test_turns_have_required_keys(self):
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "Hello"}},
            {"sequence": 2, "event_type": "assistant.response", "payload": {"preview": "Hi!"}},
        ]
        turns = _extract_turns(events)
        assert len(turns) == 1
        turn = turns[0]
        assert "user_text" in turn
        assert "assistant_text" in turn
        assert "sequence" in turn


class TestRebuildGraph:
    @pytest.mark.asyncio
    async def test_empty_events_returns_empty_graph(self):
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())
        result = await rebuild_graph(thread_id=thread_id, engine=engine, events=[])
        assert isinstance(result, RebuildResult)
        assert result.turns_replayed == 0
        assert result.graph is not None

    @pytest.mark.asyncio
    async def test_replays_turns_and_builds_graph(self):
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "I need ghostwriting help"}},
            {"sequence": 2, "event_type": "assistant.response", "payload": {"preview": "Sure, what genre?"}},
            {"sequence": 3, "event_type": "user.message", "payload": {"text": "Fantasy, 80000 words"}},
            {"sequence": 4, "event_type": "assistant.response", "payload": {"preview": "Great choice!"}},
        ]
        result = await rebuild_graph(thread_id=thread_id, engine=engine, events=events)
        assert result.turns_replayed == 2
        assert len(result.errors) == 0
        # Graph should exist
        assert result.graph is not None

    @pytest.mark.asyncio
    async def test_rebuild_result_dataclass_fields(self):
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())
        result = await rebuild_graph(thread_id=thread_id, engine=engine, events=[])
        assert hasattr(result, "graph")
        assert hasattr(result, "turns_replayed")
        assert hasattr(result, "errors")
        assert hasattr(result, "skipped_events")

    @pytest.mark.asyncio
    async def test_single_turn_replayed(self):
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "Hello"}},
            {"sequence": 2, "event_type": "assistant.response", "payload": {"preview": "Hi!"}},
        ]
        result = await rebuild_graph(thread_id=thread_id, engine=engine, events=events)
        assert result.turns_replayed == 1
        assert result.skipped_events == 0

    @pytest.mark.asyncio
    async def test_errors_list_empty_on_success(self):
        thread_id = uuid4()
        engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())
        events = [
            {"sequence": 1, "event_type": "user.message", "payload": {"text": "Test"}},
            {"sequence": 2, "event_type": "assistant.response", "payload": {"preview": "Response"}},
        ]
        result = await rebuild_graph(thread_id=thread_id, engine=engine, events=events)
        assert isinstance(result.errors, list)
        assert len(result.errors) == 0
