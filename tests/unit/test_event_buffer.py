"""Tests for the _EventBuffer class and batch event processing."""
from __future__ import annotations

import inspect

import pytest
from uuid import uuid4

from bookcraft.services.chat import _EventBuffer, _IMMEDIATE_EVENT_TYPES


class TestEventBuffer:
    def test_buffer_starts_empty(self):
        buf = _EventBuffer()
        assert buf.events == []

    def test_collect_adds_event(self):
        buf = _EventBuffer()
        thread_id = uuid4()
        event_hash, new_seq, returned_hash = buf.collect(
            thread_id=thread_id,
            sequence=0,
            event_type="intent.classified",
            payload={"intent": "ghostwriting"},
            previous_hash=None,
        )
        assert len(buf.events) == 1
        assert event_hash is not None
        assert new_seq == 1
        assert returned_hash == event_hash

    def test_collect_increments_sequence(self):
        buf = _EventBuffer()
        thread_id = uuid4()
        _, seq1, hash1 = buf.collect(
            thread_id=thread_id,
            sequence=0,
            event_type="event1",
            payload={},
            previous_hash=None,
        )
        _, seq2, _ = buf.collect(
            thread_id=thread_id,
            sequence=seq1,
            event_type="event2",
            payload={},
            previous_hash=hash1,
        )
        assert seq1 == 1
        assert seq2 == 2

    def test_collect_chains_hashes(self):
        buf = _EventBuffer()
        thread_id = uuid4()
        hash1, _, _ = buf.collect(
            thread_id=thread_id,
            sequence=0,
            event_type="event1",
            payload={},
            previous_hash=None,
        )
        _, _, _ = buf.collect(
            thread_id=thread_id,
            sequence=1,
            event_type="event2",
            payload={},
            previous_hash=hash1,
        )
        # Second event's previous_hash should be first event's hash
        assert buf.events[1]["previous_hash"] == hash1

    def test_multiple_events_collected(self):
        buf = _EventBuffer()
        thread_id = uuid4()
        prev_hash = None
        for i in range(5):
            _, seq, prev_hash = buf.collect(
                thread_id=thread_id,
                sequence=i,
                event_type=f"event_{i}",
                payload={},
                previous_hash=prev_hash,
            )
        assert len(buf.events) == 5

    def test_event_has_required_keys(self):
        buf = _EventBuffer()
        thread_id = uuid4()
        buf.collect(
            thread_id=thread_id,
            sequence=0,
            event_type="test.event",
            payload={"foo": "bar"},
            previous_hash=None,
        )
        event = buf.events[0]
        assert "sequence" in event
        assert "event_type" in event
        assert "payload" in event
        assert "previous_hash" in event
        assert "event_hash" in event

    def test_event_type_stored(self):
        buf = _EventBuffer()
        thread_id = uuid4()
        buf.collect(
            thread_id=thread_id,
            sequence=0,
            event_type="trimatch.voted",
            payload={},
            previous_hash=None,
        )
        assert buf.events[0]["event_type"] == "trimatch.voted"

    def test_payload_stored(self):
        buf = _EventBuffer()
        thread_id = uuid4()
        buf.collect(
            thread_id=thread_id,
            sequence=0,
            event_type="test",
            payload={"key": "value"},
            previous_hash=None,
        )
        assert buf.events[0]["payload"] == {"key": "value"}

    def test_hash_is_string(self):
        buf = _EventBuffer()
        thread_id = uuid4()
        event_hash, _, _ = buf.collect(
            thread_id=thread_id,
            sequence=0,
            event_type="test",
            payload={},
            previous_hash=None,
        )
        assert isinstance(event_hash, str)
        assert len(event_hash) > 0


class TestImmediateEventTypes:
    def test_user_message_is_immediate(self):
        assert "user.message" in _IMMEDIATE_EVENT_TYPES

    def test_intent_classified_not_immediate(self):
        assert "intent.classified" not in _IMMEDIATE_EVENT_TYPES

    def test_trimatch_voted_not_immediate(self):
        assert "trimatch.voted" not in _IMMEDIATE_EVENT_TYPES

    def test_is_frozenset(self):
        assert isinstance(_IMMEDIATE_EVENT_TYPES, frozenset)


class TestAppendEventsBatch:
    """Test ThreadRepository.append_events_batch method exists and has correct signature."""

    def test_batch_method_exists(self):
        from bookcraft.components.storage.thread_repository import ThreadRepository
        assert hasattr(ThreadRepository, "append_events_batch")

    def test_batch_signature(self):
        from bookcraft.components.storage.thread_repository import ThreadRepository
        sig = inspect.signature(ThreadRepository.append_events_batch)
        assert "thread_id" in sig.parameters
        assert "events" in sig.parameters

    def test_batch_method_is_coroutine(self):
        from bookcraft.components.storage.thread_repository import ThreadRepository
        assert inspect.iscoroutinefunction(ThreadRepository.append_events_batch)
