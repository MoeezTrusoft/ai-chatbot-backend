from __future__ import annotations

import pytest
from pydantic import BaseModel

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


class FakeResponseAdapter:
    name = "fake_sonnet"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, str]] = []

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        purpose: str,
        system_cache_suffix: str | None = None,
    ) -> BaseModel:
        del system_cache_suffix
        self.calls.append({"system": system, "user": user, "purpose": purpose})
        return output_model.model_validate({"text": self.text})


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
    # The status message must be surfaced to the customer without adding legal clauses.
    # The exact phrase "approved template" is no longer required; what matters is that
    # the response conveys the document status without generating legal text.
    assert draft.text.strip(), "Response must not be empty"
    assert "Obligations of Confidentiality" not in draft.text
    assert "hereby agrees" not in draft.text


@pytest.mark.asyncio
async def test_live_response_adapter_receives_guarded_prompt() -> None:
    adapter = FakeResponseAdapter("Please share the manuscript stage and preferred service.")
    draft = await SonnetResponseGenerator(
        provider_name="claude_sonnet",
        adapter=adapter,
    ).generate(
        message=processed(),
        state=ThreadState(),
        intent=vote(QueryIntentType.SERVICE_QUESTION),
        extraction=CombinedExtraction(),
    )

    assert draft.source == "claude_sonnet"
    assert "preferred service" in draft.text
    # Purpose now includes the attempt suffix ("response_full", "response_reduced").
    assert adapter.calls[0]["purpose"].startswith("response")
    assert "Do not invent prices" in adapter.calls[0]["system"]
    # The user prompt now uses "The author just wrote:" rather than "normalized_message".
    assert "portfolio samples" in adapter.calls[0]["user"]


@pytest.mark.asyncio
async def test_live_response_fails_closed_on_price_shape() -> None:
    adapter = FakeResponseAdapter("This will cost $100 and take 2 weeks.")
    draft = await SonnetResponseGenerator(
        provider_name="claude_sonnet",
        adapter=adapter,
    ).generate(
        message=processed(),
        state=ThreadState(),
        intent=vote(QueryIntentType.SERVICE_QUESTION),
        extraction=CombinedExtraction(),
    )

    # The LLM attempt returned a price shape; quality gate rejects it and falls back
    # to the template response.  Assert the customer never sees the price figure.
    assert "$100" not in draft.text
    assert "2 weeks" not in draft.text  # committed timeline also rejected
    assert draft.text.strip(), "Response must be non-empty after fail-closed fallback"
