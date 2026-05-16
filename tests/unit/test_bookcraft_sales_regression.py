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
    assert "same scope" in text
    assert "word count" in text
    assert draft.text.strip().endswith("?")
