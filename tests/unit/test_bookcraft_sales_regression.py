import pytest

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.components.response.generator import SonnetResponseGenerator
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState


def _message(text: str) -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text,
        language="en",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={
            "services": [
                "editing_proofreading",
                "interior_formatting",
                "marketing_promotion",
            ]
        },
        embedding=[],
        char_count=len(text),
    )


def _intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        query_secondary=[],
        service_primary=ServiceCategory.EDITING_PROOFREADING,
        service_secondary=[
            ServiceCategory.INTERIOR_FORMATTING,
            ServiceCategory.MARKETING_PROMOTION,
        ],
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=True,
        confidence=0.98,
        rationale="test",
        evidence=[],
    )


def _rag_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="chunk-1",
        source_id="editing-proofreading",
        title="Editing & Proofreading",
        section="overview",
        citation="Editing & Proofreading, section: overview",
        content=(
            "ks need before publication |\n"
            "| Proofreading | Final polish | After formatting |\n"
            "## Related Services\n"
            "Source: Editing & Proofreading, section: overview."
        ),
        score=0.9,
        checksum="abc",
    )


@pytest.mark.asyncio
async def test_rag_fallback_is_humanized_not_raw_doc_dump() -> None:
    generator = SonnetResponseGenerator()
    draft = await generator.generate(
        message=_message("I need editing, formatting, and marketing for my manuscript."),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(),
        rag_chunks=[_rag_chunk()],
        runtime_atoms={
            "services": [
                "editing_proofreading",
                "interior_formatting",
                "marketing_promotion",
            ]
        },
    )

    text = draft.text
    assert "Source:" not in text
    assert "##" not in text
    assert "|" not in text
    assert "ks need before publication" not in text
    assert "editing" in text.lower()
    assert "formatting" in text.lower()
    assert "marketing" in text.lower()
    assert text.strip().endswith("?")


@pytest.mark.asyncio
async def test_repeat_message_gets_progressive_next_step() -> None:
    generator = SonnetResponseGenerator()
    draft = await generator.generate(
        message=_message("I need editing, formatting, and marketing for my manuscript."),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(),
        rag_chunks=[_rag_chunk()],
        runtime_atoms={
            "services": [
                "editing_proofreading",
                "interior_formatting",
                "marketing_promotion",
            ]
        },
        response_hint="repeat_message",
    )

    text = draft.text.lower()
    assert "project still looks like" in text
    assert "word count" in text
    assert draft.text.strip().endswith("?")


@pytest.mark.asyncio
async def test_sales_response_never_mentions_internal_system_terms() -> None:
    generator = SonnetResponseGenerator()
    for query_primary in [
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
        QueryIntentType.NDA_REQUEST,
        QueryIntentType.PORTFOLIO_REQUEST,
    ]:
        intent = _intent()
        intent.query_primary = query_primary

        draft = await generator.generate(
            message=_message("I need pricing, timeline, samples, and NDA help."),
            state=ThreadState(),
            intent=intent,
            extraction=CombinedExtraction(),
            rag_chunks=[_rag_chunk()],
            runtime_atoms={
                "services": [
                    "editing_proofreading",
                    "interior_formatting",
                    "marketing_promotion",
                ]
            },
        )

        lower = draft.text.lower()
        for forbidden in [
            "engine",
            "queue",
            "tool",
            "deterministic",
            "rag",
            "classifier",
            "backend",
            "approved template",
        ]:
            assert forbidden not in lower
        assert draft.text.strip().endswith("?")


def test_mock_response_never_returns_raw_rag_source() -> None:
    intent = _intent()
    text = SonnetResponseGenerator._mock_response(
        intent=intent,
        rag_chunks=[_rag_chunk()],
        route_name="direct_answer",
    )

    assert "Source:" not in text
    assert "##" not in text
    assert "|" not in text
    assert text.strip().endswith("?")


def test_forbidden_generation_blocks_committed_prices_and_timelines() -> None:
    from bookcraft.components.response.generator import _contains_forbidden_generation

    blocked = [
        "$999",
        "USD 1,200",
        "20% discount",
        "ready in 5 days",
        "delivered in 2 weeks",
        "10 day turnaround",
        "10-day turnaround",
        "turnaround is 10 business days",
        "guaranteed in 3 weeks",
    ]

    for text in blocked:
        assert _contains_forbidden_generation(text), text


def test_forbidden_generation_allows_neutral_consultative_duration_language() -> None:
    from bookcraft.components.response.generator import _contains_forbidden_generation

    allowed = [
        "I can look at this for a week before giving a proper recommendation.",
        "A project like this usually needs careful review over a few weeks.",
        "Once I see the manuscript, I can guide you properly.",
        "The timeline depends on word count, genre, and manuscript condition.",
        "Share the deadline you are hoping for and I will help scope it.",
        "We should review the manuscript stage before discussing timing.",
    ]

    for text in allowed:
        assert not _contains_forbidden_generation(text), text
