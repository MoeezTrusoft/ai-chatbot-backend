"""Live (real Claude API) tests that the bot is date/time-aware and never books the past.

Audited transcript (chat 6070): the bot confirmed a consultation for "Wednesday, June 17"
(the chat-start date) when the customer asked for "Monday the 22nd", and assumed a
timezone without confirming. Root causes were (a) the LLM had no notion of "now" and
(b) the parser silently rolled past dates forward. These tests fire REAL Anthropic
requests through the production SonnetResponseGenerator and assert against actual model
output that:

  1. when the scheduling engine reports the requested time is in the past, the model
     relays that and asks for an upcoming slot — it does NOT claim the booking succeeded.
  2. the model does not fabricate a "confirmed" closing for a date the engine rejected.

Run before a production deploy:
    ANTHROPIC_API_KEY=... PYTHONHASHSEED=0 \
      uv run --with pytest --with pytest-asyncio python -m pytest \
      tests/integration/test_live_consultation_date_awareness.py -v -s

Requires ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


@pytest.fixture(autouse=True)
async def _reset_shared_llm_client():
    """Reset the process-wide httpx client around each test (see the RAG live test)."""
    from bookcraft.components.llm.adapters import close_shared_client

    await close_shared_client()
    yield
    await close_shared_client()


def _make_live_generator():
    from bookcraft.components.llm.adapters import AnthropicAdapter
    from bookcraft.components.response.generator import SonnetResponseGenerator
    from bookcraft.infra.config import Settings

    settings = Settings()
    adapter = AnthropicAdapter(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
        timeout_seconds=settings.llm_request_timeout_seconds,
        model=settings.anthropic_sonnet_model,
    )
    return SonnetResponseGenerator(provider_name="claude_sonnet", adapter=adapter)


def _processed(text: str):
    from bookcraft.components.preprocessor.schemas import ProcessedMessage

    return ProcessedMessage(
        raw=text,
        normalized=text.lower(),
        language="en",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[],
        char_count=len(text),
    )


def _intent():
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.domain.enums import QueryIntentType, SalesStage

    return IntentVote(
        query_primary=QueryIntentType.CONSULTATION_REQUEST,
        service_primary=None,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        confidence=0.95,
        needs_clarification=False,
        rationale="test",
        evidence=["test"],
    )


async def _generate(generator, *, message, document_status_message=None):
    from bookcraft.components.extraction.schemas import CombinedExtraction
    from bookcraft.domain.state import ThreadState

    return await generator.generate(
        message=_processed(message),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(),
        rag_chunks=[],
        portfolio_response=None,
        document_status_message=document_status_message,
    )


_PAST_MESSAGE = "Can you book my consultation for January 5th, 2020 at 2pm?"

# The exact clarifying message the dispatcher now returns for a past date.
_PAST_STATUS = (
    "That time has already passed — our specialists are available Monday–Friday, "
    "10 AM to 7 PM Central Time. What upcoming day and time works best for you?"
)

_BOOKED_WORDS = ("you're all set", "you are all set", "confirmed", "locked in", "booked")


@pytest.mark.asyncio
async def test_live_bot_does_not_confirm_a_past_date() -> None:
    generator = _make_live_generator()
    draft = await _generate(
        generator,
        message=_PAST_MESSAGE,
        document_status_message=_PAST_STATUS,
    )
    text = draft.text.lower()

    # It must not claim the (rejected) booking succeeded.
    assert not any(w in text for w in _BOOKED_WORDS), draft.text
    # It should steer toward an upcoming/future time.
    assert any(
        w in text for w in ("upcoming", "future", "another", "different", "passed", "works best")
    ), draft.text


@pytest.mark.asyncio
async def test_live_bot_relays_past_clarification_not_silent_booking() -> None:
    generator = _make_live_generator()
    draft = await _generate(
        generator,
        message="Schedule me for yesterday afternoon then.",
        document_status_message=_PAST_STATUS,
    )
    text = draft.text.lower()
    assert not any(w in text for w in _BOOKED_WORDS), draft.text
