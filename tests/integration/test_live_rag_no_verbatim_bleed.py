"""Live (real Claude API) tests that the RAG/document leak from chat 6211 cannot recur.

Chat 6211 produced "Welcome to BookCraft! <verbatim FAQ prose about trim sizes> ..."
because retrieved knowledge-base text reached the customer un-paraphrased. These tests
fire REAL Anthropic requests through the production SonnetResponseGenerator with FAQ
chunks as grounding context, then assert — against the actual model output — that:

  1. the reply does not reproduce a long verbatim span from the retrieved chunk, and
  2. the ResponseQualityGate's verbatim-bleed check (Check 24) does not fire, i.e. the
     real LLM paraphrased rather than copied; and if it ever did copy, the gate catches it.

Run before a production deploy:
    ANTHROPIC_API_KEY=... PYTHONHASHSEED=0 \
      uv run --with pytest --with pytest-asyncio python -m pytest \
      tests/integration/test_live_rag_no_verbatim_bleed.py -v -s

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
    """AnthropicAdapter shares a process-wide httpx client bound to the loop that first
    created it; pytest-asyncio uses a fresh loop per test, which otherwise yields
    'Event loop is closed' on later tests. Reset around each test so the LLM is cleanly
    exercised every time."""
    from bookcraft.components.llm.adapters import close_shared_client

    await close_shared_client()
    yield
    await close_shared_client()


# ── Builders ──────────────────────────────────────────────────────────────────
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


def _chunk(content: str, *, section: str = "formatting", source_id: str = "formatting_faq"):
    from bookcraft.components.rag.schemas import RetrievedChunk

    return RetrievedChunk(
        chunk_id=f"{source_id}-1",
        content=content,
        score=0.92,
        section=section,
        source_id=source_id,
        title="Formatting FAQ",
        checksum="x",
        citation="Formatting FAQ",
    )


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


def _intent(query):
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.domain.enums import SalesStage

    return IntentVote(
        query_primary=query,
        service_primary=None,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        confidence=0.95,
        needs_clarification=False,
        rationale="test",
        evidence=["test"],
    )


# The exact knowledge-base prose that bled into chat 6211.
_TRIM_SIZE_FAQ = (
    "Will you advise on the best trim size for my book? Yes. Trim size matters for "
    "genre conventions (mass-market paperbacks are 4.25x6.87; trade paperbacks are "
    "typically 5.5x8.5 or 6x9; literary fiction often uses 5.25x8) and for cost "
    "efficiency at print-on-demand. We also handle embedded Arabic quotations, for example."
)

_PRICING_FAQ = (
    "What influences cost? Beyond genre and engagement model, these factors can affect "
    "your quote: content complexity drivers, specialized non-fiction research, the "
    "manuscript's current condition, and your deadline. Editing and proofreading is the "
    "main direction here for most polished drafts."
)


async def _generate(generator, *, message, query, chunks):
    from bookcraft.components.extraction.schemas import CombinedExtraction
    from bookcraft.domain.state import ThreadState

    return await generator.generate(
        message=_processed(message),
        state=ThreadState(),
        intent=_intent(query),
        extraction=CombinedExtraction(),
        rag_chunks=chunks,
        portfolio_response=None,
        document_status_message=None,
    )


def _assert_no_verbatim_bleed(draft_text: str, chunks) -> None:
    from bookcraft.components.response.quality_gate import _verbatim_rag_overlap

    span = _verbatim_rag_overlap(draft_text, chunks)
    assert span is None, (
        f"Real LLM reply reproduced a verbatim span from the RAG chunk: {span!r}\n"
        f"Full reply: {draft_text!r}"
    )
    # No raw FAQ markers either.
    lowered = draft_text.lower()
    assert "will you advise on the best trim size" not in lowered
    assert "4.25x6.87" not in lowered


# ── Tests ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_trim_size_faq_is_paraphrased_not_copied():
    """Customer asks about trim size; FAQ chunk is grounding context. Real reply must
    paraphrase, never copy the chunk verbatim (the chat 6211 failure mode)."""
    from bookcraft.domain.enums import QueryIntentType

    generator = _make_live_generator()
    chunks = [_chunk(_TRIM_SIZE_FAQ)]
    draft = await _generate(
        generator,
        message="I'm formatting my dark fantasy novel — what trim size should I use?",
        query=QueryIntentType.SERVICE_QUESTION,
        chunks=chunks,
    )
    print(f"\n[trim-size] source={draft.source}\nreply={draft.text}\n")
    assert draft.text and len(draft.text) > 20
    _assert_no_verbatim_bleed(draft.text, chunks)


@pytest.mark.asyncio
async def test_pricing_faq_is_paraphrased_not_copied():
    """A second document (pricing FAQ) — same guarantee, different source text."""
    from bookcraft.domain.enums import QueryIntentType

    generator = _make_live_generator()
    chunks = [_chunk(_PRICING_FAQ, section="pricing", source_id="pricing_faq")]
    draft = await _generate(
        generator,
        message="What affects the cost of editing my manuscript?",
        query=QueryIntentType.PRICING_QUESTION,
        chunks=chunks,
    )
    print(f"\n[pricing] source={draft.source}\nreply={draft.text}\n")
    assert draft.text and len(draft.text) > 20
    _assert_no_verbatim_bleed(draft.text, chunks)


@pytest.mark.asyncio
async def test_live_reply_passes_quality_gate_verbatim_check():
    """End-to-end: the real reply + the same chunks must clear the quality gate's
    verbatim-bleed check (Check 24)."""
    from bookcraft.components.response.quality_gate import ResponseQualityGate
    from bookcraft.domain.enums import QueryIntentType
    from bookcraft.domain.state import ThreadState

    generator = _make_live_generator()
    chunks = [_chunk(_TRIM_SIZE_FAQ)]
    draft = await _generate(
        generator,
        message="Help me pick a trim size for my fantasy paperback.",
        query=QueryIntentType.SERVICE_QUESTION,
        chunks=chunks,
    )
    report = ResponseQualityGate().evaluate(
        text=draft.text,
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=ThreadState(),
        rag_chunks=chunks,
    )
    print(f"\n[gate] source={draft.source} failures={report.failures}\nreply={draft.text}\n")
    assert not any("verbatim_rag_document_bleed" in f for f in report.failures), (
        f"Quality gate flagged verbatim bleed in the live reply: {draft.text!r}"
    )
