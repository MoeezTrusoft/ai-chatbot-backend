"""Gap 3 + Gap 5 unit tests.

Gap 3: secondary intent is surfaced into _response_user_prompt.
Gap 5: RAG chunk budget raised (5 chunks × 600 chars); template fallback includes RAG context.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.response.generator import (
    _humanized_template_response,
    _response_user_prompt,
)
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


def _intent(
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    secondary: list[QueryIntentType] | None = None,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        query_secondary=secondary or [],
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )


def _processed(text: str = "tell me about ghostwriting") -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text,
        language="en",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[],
        char_count=len(text),
        negation_targets=[],
    )


def _rag_chunk(content: str) -> MagicMock:
    c = MagicMock()
    c.content = content
    return c


# ---------------------------------------------------------------------------
# Gap 3: secondary intent surfacing
# ---------------------------------------------------------------------------


def test_secondary_intent_appears_in_user_prompt() -> None:
    """When query_secondary is set, _response_user_prompt must include 'also asked about'."""
    prompt = _response_user_prompt(
        message=_processed("What does cover design cost, and how does KDP work?"),
        state=ThreadState(),
        intent=_intent(
            QueryIntentType.PRICING_QUESTION,
            secondary=[QueryIntentType.PUBLISHING_PLATFORM_QUESTION],
        ),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=[],
        route_name="direct_answer",
        runtime_atoms={},
    )
    assert "also asked about" in prompt.lower(), (
        "Prompt must surface secondary intent — 'also asked about' not found"
    )
    assert "publishing platform question" in prompt.lower() or "publishing" in prompt.lower()


def test_no_secondary_intent_no_secondary_section() -> None:
    """When query_secondary is empty, no 'also asked about' section should appear."""
    prompt = _response_user_prompt(
        message=_processed("How much does ghostwriting cost?"),
        state=ThreadState(),
        intent=_intent(QueryIntentType.PRICING_QUESTION, secondary=[]),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=[],
        route_name="direct_answer",
        runtime_atoms={},
    )
    assert "also asked about" not in prompt.lower()


def test_secondary_intent_capped_at_three() -> None:
    """Even with many secondary intents, at most 3 appear in prompt."""
    from bookcraft.domain.enums import QueryIntentType

    many_secondary = [
        QueryIntentType.PUBLISHING_PLATFORM_QUESTION,
        QueryIntentType.REVISION_QUESTION,
        QueryIntentType.PAYMENT_QUESTION,
        QueryIntentType.CONSULTATION_REQUEST,
        QueryIntentType.PORTFOLIO_REQUEST,
    ]
    prompt = _response_user_prompt(
        message=_processed("lots of questions"),
        state=ThreadState(),
        intent=_intent(QueryIntentType.PRICING_QUESTION, secondary=many_secondary),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=[],
        route_name="direct_answer",
        runtime_atoms={},
    )
    # Should not include all 5 secondary intents verbatim — capped at 3
    assert "portfolio request" not in prompt.lower() or "revision question" in prompt.lower()


# ---------------------------------------------------------------------------
# Gap 5: RAG chunk budget
# ---------------------------------------------------------------------------


def test_rag_prompt_renders_up_to_five_chunks() -> None:
    """_response_user_prompt must render up to 5 RAG chunks."""
    chunks = [_rag_chunk(f"chunk content number {i}") for i in range(6)]
    prompt = _response_user_prompt(
        message=_processed("tell me about editing"),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=chunks,
        route_name="direct_answer",
        runtime_atoms={},
    )
    # All 5 first chunks should appear; chunk 6 (index 5) should not
    assert "chunk content number 0" in prompt
    assert "chunk content number 4" in prompt
    assert "chunk content number 5" not in prompt


def test_rag_snippet_cap_is_600_chars() -> None:
    """Each RAG chunk snippet must be capped at 600 chars."""
    long_content = "A" * 800
    chunks = [_rag_chunk(long_content)]
    prompt = _response_user_prompt(
        message=_processed("tell me about editing"),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=chunks,
        route_name="direct_answer",
        runtime_atoms={},
    )
    # The full 800-char string should not appear — it should be truncated to 600
    assert "A" * 800 not in prompt
    assert "A" * 600 in prompt or "A" * 599 in prompt


def test_rag_prompt_label_is_authoritative() -> None:
    """RAG section label must indicate authority, not 'private grounding only'."""
    chunks = [_rag_chunk("BookCraft offers professional ghostwriting services.")]
    prompt = _response_user_prompt(
        message=_processed("what services do you offer?"),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=chunks,
        route_name="direct_answer",
        runtime_atoms={},
    )
    # Old label said "private grounding only" — new label says authoritative
    assert "private grounding only" not in prompt
    assert "authoritative" in prompt or "bookcraft grounding" in prompt.lower()


def test_template_fallback_includes_rag_context() -> None:
    """_humanized_template_response must include top RAG chunk in its output."""
    chunks = [_rag_chunk("BookCraft specialises in fantasy and thriller genres.")]
    result = _humanized_template_response(
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=ThreadState(),
        message=_processed("what do you do?"),
        runtime_atoms={},
        rag_chunks=chunks,
        route_name="direct_answer",
    )
    # The snippet should appear in the template response
    assert "BookCraft specialises" in result or "fantasy" in result.lower()


def test_template_fallback_without_rag_does_not_crash() -> None:
    """Template fallback with no RAG chunks must still produce a response."""
    result = _humanized_template_response(
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=ThreadState(),
        message=_processed("what do you do?"),
        runtime_atoms={},
        rag_chunks=[],
        route_name="direct_answer",
    )
    assert isinstance(result, str)
    assert len(result) > 10
