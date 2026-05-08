from __future__ import annotations

import pytest

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.portfolio.schemas import (
    PortfolioMediaType,
    PortfolioResponse,
    PortfolioSample,
    PortfolioStatus,
)
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.response import ResponseFormatter, ResponseRouter, SonnetResponseGenerator
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState


def vote(query: QueryIntentType) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
    )


def processed() -> ProcessedMessage:
    return ProcessedMessage(
        raw="portfolio samples",
        normalized="portfolio samples",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[],
        language="en",
        char_count=17,
    )


def test_response_router_maps_controlled_routes() -> None:
    router = ResponseRouter()

    assert router.route(vote(QueryIntentType.PORTFOLIO_REQUEST)).name == "portfolio"
    assert router.route(vote(QueryIntentType.NDA_REQUEST)).name == "nda"
    assert router.route(vote(QueryIntentType.AGREEMENT_REQUEST)).name == "agreement"
    assert router.route(vote(QueryIntentType.PRICING_QUESTION)).name == "price_timeline"


def test_formatter_only_marks_approved_urls_as_rich_segments() -> None:
    text = "Approved: https://approved.example/a Unapproved: https://unapproved.example/b"
    bubbles = ResponseFormatter().format(text, approved_urls={"https://approved.example/a"})

    urls = [segment["text"] for segment in bubbles[0].rich_segments if segment["type"] == "url"]
    assert urls == ["https://approved.example/a"]


@pytest.mark.asyncio
async def test_portfolio_response_uses_registry_output_only() -> None:
    sample = PortfolioSample(
        title="Sample Cover",
        service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        genre="fantasy",
        url="https://registry.example/sample",
        cover_image=None,
        media_type=PortfolioMediaType.EXTERNAL_LINK,
        reason_selected="Registry-backed sample.",
        source_id="fixture:1",
    )

    draft = await SonnetResponseGenerator().generate(
        message=processed(),
        state=ThreadState(),
        intent=vote(QueryIntentType.PORTFOLIO_REQUEST),
        extraction=CombinedExtraction(),
        portfolio_response=PortfolioResponse(
            service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
            requested_genre="fantasy",
            status=PortfolioStatus.FOUND,
            samples=[sample],
            message="Returned approved registry samples only.",
            registry_version="test",
        ),
    )

    assert "Sample Cover" in draft.text
    assert draft.approved_urls == ["https://registry.example/sample"]


@pytest.mark.asyncio
async def test_document_status_never_generates_legal_text() -> None:
    draft = await SonnetResponseGenerator().generate(
        message=processed(),
        state=ThreadState(),
        intent=vote(QueryIntentType.NDA_REQUEST),
        extraction=CombinedExtraction(),
        document_status_message="NDA text must render from the approved template only.",
    )

    assert draft.source == "nda"
    assert "approved template" in draft.text
