"""Tests for handle_inject_facts retry behaviour on ThreadVersionConflictError.

The fix: inject_facts now retries up to 3x with exponential backoff when it
encounters a version conflict, rather than propagating a 500 error.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from bookcraft.api.chat import ChatFactsRequest


def _make_service():
    from bookcraft.services.chat import ChatService
    from bookcraft.components.extraction import StateApplier
    from bookcraft.components.response.generator import SonnetResponseGenerator

    svc = ChatService.__new__(ChatService)
    svc.thread_repository = None  # use in-memory dict
    svc.threads = {}
    svc.state_applier = StateApplier()
    svc.response_generator = SonnetResponseGenerator()
    return svc


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inject_facts_fills_empty_fields():
    svc = _make_service()
    tid = uuid4()

    result = await svc.handle_inject_facts(
        ChatFactsRequest(thread_id=tid, name="Jake Biddulph", email="jake@example.com",
                         phone=None, source_label="test")
    )
    assert "name" in result.fields_applied
    assert "email" in result.fields_applied
    assert "phone" not in result.fields_applied
    assert svc.threads[tid].state.personal.name.value == "Jake Biddulph"
    assert svc.threads[tid].state.personal.email.value == "jake@example.com"


@pytest.mark.asyncio
async def test_inject_facts_does_not_overwrite_high_confidence():
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta
    from bookcraft.services.chat import ThreadMemory

    svc = _make_service()
    tid = uuid4()
    mem = svc.threads.setdefault(tid, ThreadMemory())
    mem.state.personal.name = FieldMeta(value="Chris Jordan", confidence=0.99, source=Source.AI_EXTRACTED)

    result = await svc.handle_inject_facts(
        ChatFactsRequest(thread_id=tid, name="Wrong Name", email=None, phone=None, source_label="test")
    )
    # 0.98 < 0.99 → not applied
    assert "name" not in result.fields_applied
    assert svc.threads[tid].state.personal.name.value == "Chris Jordan"


# ---------------------------------------------------------------------------
# Retry on version conflict (mocked against in-memory path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inject_facts_succeeds_after_conflict_in_memory():
    """In-memory path never conflicts — verify retry logic path exits cleanly."""
    svc = _make_service()
    tid = uuid4()

    call_n = {"n": 0}
    original_apply = svc.state_applier.apply

    def apply_with_first_raise(state, extraction, **kw):
        call_n["n"] += 1
        if call_n["n"] == 1:
            raise Exception("version conflict: expected 0, found 1")
        return original_apply(state, extraction, **kw)

    svc.state_applier.apply = apply_with_first_raise

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await svc.handle_inject_facts(
            ChatFactsRequest(thread_id=tid, name="Jake Biddulph", email=None, phone=None, source_label="test")
        )

    assert call_n["n"] == 2
    assert "name" in result.fields_applied


@pytest.mark.asyncio
async def test_inject_facts_gives_up_after_max_retries():
    """After 3 failed attempts, returns empty fields_applied — no 500."""
    svc = _make_service()
    tid = uuid4()

    def always_conflict(state, extraction, **kw):
        raise Exception("version conflict")

    svc.state_applier.apply = always_conflict

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await svc.handle_inject_facts(
            ChatFactsRequest(thread_id=tid, name="Jake Biddulph", email=None, phone=None, source_label="test")
        )

    assert result.fields_applied == []
    assert str(result.thread_id) == str(tid)


# ---------------------------------------------------------------------------
# Empty payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inject_facts_empty_payload_returns_immediately():
    svc = _make_service()
    tid = uuid4()

    result = await svc.handle_inject_facts(
        ChatFactsRequest(thread_id=tid, name=None, email=None, phone=None, source_label="test")
    )
    assert result.fields_applied == []
    # Thread never touched
    assert tid not in svc.threads
