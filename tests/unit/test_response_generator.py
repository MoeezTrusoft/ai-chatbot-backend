import pytest

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.response.generator import SonnetResponseGenerator
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


@pytest.mark.asyncio
async def test_portfolio_pricing_nda_mixed_request_uses_deterministic_guard() -> None:
    generator = SonnetResponseGenerator(adapter=None)

    draft = await generator.generate(
        message=ProcessedMessage(
            raw="I need pricing, samples, and NDA, but do not invent links or numbers.",
            normalized="I need pricing, samples, and NDA, but do not invent links or numbers.",
            language="en",
            tokens=[],
            negation_spans=[],
            hedge_spans=[],
            counterfactual_spans=[],
            deterministic_atoms={},
            embedding=[],
            char_count=len("I need pricing, samples, and NDA, but do not invent links or numbers."),
        ),
        state=ThreadState(),
        intent=IntentVote(
            query_primary=QueryIntentType.PORTFOLIO_REQUEST,
            service_primary=None,
            funnel_stage=SalesStage.NDA_REQUESTED,
            confidence=1.0,
            needs_clarification=False,
            rationale="test",
            evidence=["test"],
        ),
        extraction=CombinedExtraction(),
        rag_chunks=[],
        portfolio_response=None,
        document_status_message=None,
    )

    assert draft.source == "deterministic_mixed_request_guard"
    assert "without guessing or sending anything generic" in draft.text
    assert "service and genre" in draft.text
    assert "word or page count" in draft.text
    assert "author name, email, phone" in draft.text
