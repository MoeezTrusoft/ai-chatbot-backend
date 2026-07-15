"""The response prompt must tell the model the truth about what it knows (chat 5876).

Two prompt-level defects produced that transcript:

1. A name pulled from a month-old signup form was listed under the header "What I can
   tell from this message", so the model reported that the author had "shared it as
   ... earlier in our chat". The model had no way to know otherwise.
2. A 13-question checklist reached the model with no instruction to answer all of it,
   and both the style policy and the quality gate reject any draft containing more
   than one '?', so a reply that restated each question before answering it was
   thrown away and the turn fell back to a canned bestseller disclaimer.

All contact data here is synthetic. This repo is public.
"""

from __future__ import annotations

import pytest

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.response.generator import _response_user_prompt
from bookcraft.domain.enums import QueryIntentType, SalesStage, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState

CHECKLIST_QUESTIONS = [
    "Who owns the ISBN?",
    "Do you keep 100% of your copyright?",
    "Are there exclusive distribution clauses?",
    "What marketing is actually guaranteed versus merely offered?",
]


def _message(text: str) -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text,
        language="en",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[1.0],
        char_count=len(text),
    )


def _intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=None,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )


def _prompt(*, state: ThreadState, text: str, runtime_atoms: dict) -> str:
    return _response_user_prompt(
        message=_message(text),
        state=state,
        intent=_intent(),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=[],
        route_name="direct_answer",
        runtime_atoms=runtime_atoms,
    )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_form_submitted_name_is_declared_as_coming_from_outside_the_chat():
    state = ThreadState()
    state.personal.name = FieldMeta(
        value="Dana Example", confidence=0.90, source=Source.EXTERNAL_FORM
    )

    prompt = _prompt(state=state, text="Where did you get my name?", runtime_atoms={})

    assert "Dana Example" in prompt
    assert "OUTSIDE this conversation" in prompt
    # The model must be told both the origin and the honest answer to give.
    assert "did NOT say this in this chat" in prompt
    assert "signup form" in prompt


def test_form_submitted_name_is_not_listed_as_a_conversation_fact():
    """The bug: it sat in the same list as things the author actually said."""
    state = ThreadState()
    state.personal.name = FieldMeta(
        value="Dana Example", confidence=0.90, source=Source.EXTERNAL_FORM
    )

    prompt = _prompt(state=state, text="hi", runtime_atoms={})

    known_line = next(
        line for line in prompt.splitlines() if "gathered over this conversation" in line
    )
    assert "Dana Example" not in known_line


def test_chat_stated_name_stays_a_conversation_fact():
    state = ThreadState()
    state.personal.name = FieldMeta(value="Dana Example", confidence=0.92, source=Source.USER_STATED)

    prompt = _prompt(state=state, text="hi", runtime_atoms={})

    assert "author name: Dana Example" in prompt
    # No false provenance disclaimer for something they really did say here.
    assert "OUTSIDE this conversation" not in prompt


def test_no_external_clause_when_nothing_was_injected():
    prompt = _prompt(state=ThreadState(), text="hi", runtime_atoms={})
    assert "OUTSIDE this conversation" not in prompt


# ---------------------------------------------------------------------------
# Multi-question contract
# ---------------------------------------------------------------------------


def test_every_question_is_listed_and_required_to_be_answered():
    prompt = _prompt(
        state=ThreadState(),
        text=" ".join(CHECKLIST_QUESTIONS),
        runtime_atoms={"questions": CHECKLIST_QUESTIONS},
    )

    assert "asked 4 separate questions" in prompt
    for question in CHECKLIST_QUESTIONS:
        assert question in prompt
    assert "Answer EVERY question above" in prompt


def test_multi_question_reply_is_told_not_to_emit_extra_question_marks():
    """Load-bearing: >1 '?' is rejected downstream and the turn loses its answer."""
    prompt = _prompt(
        state=ThreadState(),
        text=" ".join(CHECKLIST_QUESTIONS),
        runtime_atoms={"questions": CHECKLIST_QUESTIONS},
    )

    assert "do NOT put a question mark on" in prompt
    assert "40-word limit does NOT apply" in prompt


@pytest.mark.parametrize("questions", [[], ["Who owns the ISBN?"]])
def test_single_or_no_question_turns_keep_the_normal_contract(questions):
    prompt = _prompt(
        state=ThreadState(),
        text="Who owns the ISBN?",
        runtime_atoms={"questions": questions},
    )

    assert "separate questions in this one message" not in prompt
    assert "40-word limit does NOT apply" not in prompt
