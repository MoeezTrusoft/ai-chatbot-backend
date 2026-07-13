"""Tests for broadened identity detection and intent-gated RAG breadth.

- Persona: chats 6728/6759 asked "is this a live consultant?" and "is this an
  artificial intelligence" — neither matched the old regex, so no representative
  name was assigned and the bot fell back to a generic "BookCraft guide".
- RAG: non-service / meta / off-topic turns should not pull broad grounding docs
  (a source of hallucination); service-detail turns keep the full breadth.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bookcraft.components.persona.representative import (
    BookCraftPersona,
    _IDENTITY_QUESTION_RE,
)
from bookcraft.domain.enums import QueryIntentType, ServiceCategory
from bookcraft.domain.state import ThreadState
from bookcraft.services.chat import (
    _RAG_TOP_K_BROAD,
    _RAG_TOP_K_NARROW,
    _rag_top_k_for_intent,
)


# --------------------------------------------------------------------------- #
# Persona identity detection
# --------------------------------------------------------------------------- #


def test_identity_regex_matches_regressed_phrasings() -> None:
    for msg in (
        "Is this a live consultant?",
        "Also is this an artificial intelligence",
        "is this AI",
        "are you an AI",
        "are you a bot",
        "is this a real person",
        "can I talk to a human agent",
        "are you human",
        "who are you",
    ):
        assert _IDENTITY_QUESTION_RE.search(msg), msg


def test_identity_regex_ignores_normal_questions() -> None:
    for msg in (
        "How much does editing cost?",
        "I want to publish my book",
        "is this the right genre for my book",
        "can you help me with my manuscript",
    ):
        assert not _IDENTITY_QUESTION_RE.search(msg), msg


def test_identity_question_assigns_and_persists_a_name() -> None:
    persona = BookCraftPersona()
    state = ThreadState()
    decision = persona.evaluate(
        message="is this an artificial intelligence?", state=state
    )
    assert decision.is_identity_question is True
    assert decision.representative_name is not None
    # Name persists on state and is reused on the next identity question.
    assert state.representative_name == decision.representative_name
    again = persona.evaluate(message="are you a bot?", state=state)
    assert again.representative_name == decision.representative_name
    assert again.is_first_introduction is False


# --------------------------------------------------------------------------- #
# RAG intent-gated breadth
# --------------------------------------------------------------------------- #


def _intent(primary: QueryIntentType, service: ServiceCategory | None = None) -> MagicMock:
    m = MagicMock()
    m.query_primary = primary
    m.service_primary = service
    return m


def test_service_questions_get_broad_rag() -> None:
    assert _rag_top_k_for_intent(_intent(QueryIntentType.SERVICE_QUESTION)) == _RAG_TOP_K_BROAD
    assert (
        _rag_top_k_for_intent(_intent(QueryIntentType.PUBLISHING_PLATFORM_QUESTION))
        == _RAG_TOP_K_BROAD
    )


def test_meta_and_offtopic_skip_rag() -> None:
    for it in (
        QueryIntentType.GREETING,
        QueryIntentType.OFF_TOPIC,
        QueryIntentType.SPAM_OR_ABUSE,
        QueryIntentType.CONTACT_INFO_PROVIDED,
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.PORTFOLIO_REQUEST,
    ):
        assert _rag_top_k_for_intent(_intent(it)) == 0, it


def test_ambiguous_intents_get_narrow_rag() -> None:
    for it in (
        QueryIntentType.UNCLEAR,
        QueryIntentType.READY_TO_BUY,
        QueryIntentType.CONSULTATION_REQUEST,
        QueryIntentType.COMPLAINT_OR_OBJECTION,
    ):
        assert _rag_top_k_for_intent(_intent(it)) == _RAG_TOP_K_NARROW, it


def test_named_service_widens_even_ambiguous_intent() -> None:
    # A concrete service was detected — retrieval is category-filtered, so broad
    # is safe and useful.
    assert (
        _rag_top_k_for_intent(
            _intent(QueryIntentType.UNCLEAR, service=ServiceCategory.EDITING_PROOFREADING)
        )
        == _RAG_TOP_K_BROAD
    )
