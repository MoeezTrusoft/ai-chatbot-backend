import pytest

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.components.response import SonnetResponseGenerator
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState


def processed() -> ProcessedMessage:
    return ProcessedMessage(
        raw="Tell me about ghostwriting",
        normalized="Tell me about ghostwriting",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[0.0] * 384,
        language="en",
        char_count=26,
    )


@pytest.mark.asyncio
async def test_response_uses_rag_context_for_service_question() -> None:
    draft = await SonnetResponseGenerator().generate(
        message=processed(),
        state=ThreadState(),
        intent=IntentVote(
            query_primary=QueryIntentType.SERVICE_QUESTION,
            service_primary=ServiceCategory.GHOSTWRITING,
            funnel_stage=SalesStage.SERVICE_DISCOVERY,
            needs_clarification=False,
            confidence=0.9,
            rationale="test",
        ),
        extraction=CombinedExtraction(),
        rag_chunks=[
            RetrievedChunk(
                chunk_id="chunk",
                content="Ghostwriting helps authors develop a manuscript from concept to draft.",
                score=1.0,
                service_category=ServiceCategory.GHOSTWRITING,
                section="Overview",
                source_id="ghostwriting",
                title="Ghostwriting",
                checksum="checksum",
                citation="Ghostwriting::Overview::chunk",
            )
        ],
    )

    assert "Ghostwriting helps authors" in draft.text
    assert "Source: Ghostwriting" in draft.text


@pytest.mark.asyncio
async def test_response_ignores_rag_context_for_pricing_question() -> None:
    draft = await SonnetResponseGenerator().generate(
        message=processed(),
        state=ThreadState(),
        intent=IntentVote(
            query_primary=QueryIntentType.PRICING_QUESTION,
            service_primary=ServiceCategory.GHOSTWRITING,
            funnel_stage=SalesStage.QUOTE_REQUESTED,
            needs_clarification=True,
            confidence=0.9,
            rationale="test",
        ),
        extraction=CombinedExtraction(),
        rag_chunks=[
            RetrievedChunk(
                chunk_id="chunk",
                content="This RAG content should not be used for quote questions.",
                score=1.0,
                section="Overview",
                source_id="x",
                title="X",
                checksum="checksum",
                citation="x",
            )
        ],
    )

    assert "deterministic quote engine" in draft.text
    assert "This RAG content" not in draft.text

