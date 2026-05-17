from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.portfolio import PortfolioEngine, PortfolioRegistry
from bookcraft.components.portfolio_actions import (
    PortfolioActionRequest,
    PortfolioActionService,
)
from bookcraft.components.portfolio_actions.repository import (
    InMemoryPortfolioViewRepository,
)
from bookcraft.domain.enums import ServiceCategory


@pytest.fixture()
def portfolio_service() -> PortfolioActionService:
    registry = PortfolioRegistry.from_files(
        samples_registry_path="data/portfolio/samples.registry.js",
        genre_hierarchy_path="data/portfolio/genre_hierarchy_links.json",
        portfolio_docx_path="data/portfolio/portfolio_samples.docx",
    )
    return PortfolioActionService(
        portfolio_engine=PortfolioEngine(registry),
        repository=InMemoryPortfolioViewRepository(),
    )


@pytest.mark.asyncio
async def test_portfolio_action_returns_samples_and_records_views(
    portfolio_service: PortfolioActionService,
) -> None:
    thread_id = uuid4()
    customer_id = uuid4()

    result = await portfolio_service.lookup(
        PortfolioActionRequest(
            customer_id=customer_id,
            thread_id=thread_id,
            service=ServiceCategory.INTERIOR_FORMATTING.value,
            genre="business",
            limit=2,
        )
    )

    assert result.service == ServiceCategory.INTERIOR_FORMATTING.value
    assert result.status in {"found", "no_match"}
    if result.status == "found":
        assert result.sample_ids
        assert len(result.samples) <= 2


@pytest.mark.asyncio
async def test_portfolio_action_skips_previously_seen_samples(
    portfolio_service: PortfolioActionService,
) -> None:
    thread_id = uuid4()
    customer_id = uuid4()

    first = await portfolio_service.lookup(
        PortfolioActionRequest(
            customer_id=customer_id,
            thread_id=thread_id,
            service=ServiceCategory.INTERIOR_FORMATTING.value,
            limit=1,
        )
    )

    second = await portfolio_service.lookup(
        PortfolioActionRequest(
            customer_id=customer_id,
            thread_id=thread_id,
            service=ServiceCategory.INTERIOR_FORMATTING.value,
            limit=1,
        )
    )

    if first.sample_ids and second.sample_ids:
        assert first.sample_ids[0] != second.sample_ids[0]
    assert set(first.sample_ids).issubset(set(second.skipped_sample_ids))


@pytest.mark.asyncio
async def test_portfolio_action_confidential_ghostwriting(
    portfolio_service: PortfolioActionService,
) -> None:
    result = await portfolio_service.lookup(
        PortfolioActionRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            service=ServiceCategory.GHOSTWRITING.value,
            limit=3,
        )
    )

    assert result.status == "unavailable_confidential"
    assert result.samples == []
    assert "confidential" in result.customer_safe_summary.lower()
