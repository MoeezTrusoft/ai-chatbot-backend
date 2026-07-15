"""Provenance rules for contact facts injected from outside the chat (chat 5876).

A visitor signed up a month before this conversation, so the bot already held his
name when the chat opened. Asked "where did you get my name?", it answered "you
shared it earlier in our chat" — which was false. The name arrived over /chat/facts
as ``crm_session_sync``, but it was stamped ``AI_EXTRACTED`` (the same source the LLM
extractor uses for facts it reads out of chat text) and then rendered into the prompt
under the header "What I can tell from this message", so nothing downstream — code or
model — could tell it apart from something the customer had actually typed.

Separately, passively-harvested on-blur keystrokes must never reach the bot at all:
the visitor tabbed through a form field, they did not choose to tell us anything.

All contact data here is synthetic. This repo is public — never paste real customer
names, emails, or phone numbers into tests.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.api.chat import ChatFactsRequest
from bookcraft.domain.enums import Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.services.chat import (
    EXTERNAL_FORM_CONFIDENCE,
    EXTERNAL_PASSIVE_SOURCE_LABELS,
    EXTERNAL_VERIFIED_SOURCE_LABELS,
)


def _make_service():
    from bookcraft.components.extraction import StateApplier
    from bookcraft.components.response.generator import SonnetResponseGenerator
    from bookcraft.services.chat import ChatService

    svc = ChatService.__new__(ChatService)
    svc.thread_repository = None  # in-memory path
    svc.threads = {}
    svc.state_applier = StateApplier()
    svc.response_generator = SonnetResponseGenerator()
    return svc


# ---------------------------------------------------------------------------
# Provenance is recorded, not flattened into "the customer said this"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submitted_form_facts_are_stamped_external_not_ai_extracted():
    svc = _make_service()
    tid = uuid4()

    result = await svc.handle_inject_facts(
        ChatFactsRequest(
            thread_id=tid,
            name="Dana Example",
            email="dana@example.com",
            phone=None,
            source_label="crm_session_sync",
        )
    )

    assert "name" in result.fields_applied
    name = svc.threads[tid].state.personal.name
    # The whole point: downstream can distinguish this from a chat-stated fact.
    assert name.source is Source.EXTERNAL_FORM
    assert name.source is not Source.AI_EXTRACTED
    assert name.extracted_by == "external_form.crm_session_sync"


@pytest.mark.asyncio
async def test_every_verified_label_is_accepted():
    for label in EXTERNAL_VERIFIED_SOURCE_LABELS:
        svc = _make_service()
        tid = uuid4()
        result = await svc.handle_inject_facts(
            ChatFactsRequest(
                thread_id=tid, name="Dana Example", email=None, phone=None, source_label=label
            )
        )
        assert result.fields_applied == ["name"], f"verified label {label!r} was refused"


# ---------------------------------------------------------------------------
# On-blur / unknown sources are refused outright
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_onblur_capture_never_reaches_thread_state():
    svc = _make_service()
    tid = uuid4()

    result = await svc.handle_inject_facts(
        ChatFactsRequest(
            thread_id=tid,
            name="Dana Example",
            email="dana@example.com",
            phone="+1 555 0100",
            source_label="onblur_capture",
        )
    )

    assert result.fields_applied == []
    # Refused, not merely unused: the bot must not hold data the visitor never
    # chose to give it.
    assert svc.threads.get(tid) is None or svc.threads[tid].state.personal.name.value is None


@pytest.mark.asyncio
async def test_unknown_source_label_fails_closed():
    svc = _make_service()
    tid = uuid4()

    result = await svc.handle_inject_facts(
        ChatFactsRequest(
            thread_id=tid, name="Dana Example", email=None, phone=None, source_label="test"
        )
    )

    # An unreviewed caller cannot smuggle contact data in by inventing a label.
    assert result.fields_applied == []


def test_passive_labels_are_not_also_verified():
    assert not (EXTERNAL_PASSIVE_SOURCE_LABELS & EXTERNAL_VERIFIED_SOURCE_LABELS)


# ---------------------------------------------------------------------------
# Chat outranks the CRM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_name_stated_in_chat_outranks_the_signup_record():
    """A correction in chat must win over a months-old signup row.

    At the old 0.98 the injected value outranked contact_capture_sync's 0.92, so
    "actually, it's Mike" was silently discarded in favour of the CRM's copy.
    """
    from bookcraft.services.chat import ThreadMemory

    svc = _make_service()
    tid = uuid4()
    mem = svc.threads.setdefault(tid, ThreadMemory())
    # What contact_capture_sync writes when the customer states a name in chat.
    mem.state.personal.name = FieldMeta(
        value="Mike", confidence=0.92, source=Source.USER_STATED
    )

    result = await svc.handle_inject_facts(
        ChatFactsRequest(
            thread_id=tid,
            name="Michael From The Signup Form",
            email=None,
            phone=None,
            source_label="signup_form",
        )
    )

    assert "name" not in result.fields_applied
    assert svc.threads[tid].state.personal.name.value == "Mike"


def test_external_confidence_sits_below_every_chat_stated_confidence():
    # Guards the ordering the test above depends on: deterministic email 0.98,
    # phone 0.92, contact_capture_sync name 0.92.
    assert EXTERNAL_FORM_CONFIDENCE < 0.92


@pytest.mark.asyncio
async def test_external_facts_still_fill_an_empty_field():
    """The gate must not throw away legitimate data — only mislabel-proof it."""
    svc = _make_service()
    tid = uuid4()

    result = await svc.handle_inject_facts(
        ChatFactsRequest(
            thread_id=tid,
            name="Dana Example",
            email=None,
            phone=None,
            source_label="signup_form",
        )
    )

    assert result.fields_applied == ["name"]
    assert svc.threads[tid].state.personal.name.value == "Dana Example"
