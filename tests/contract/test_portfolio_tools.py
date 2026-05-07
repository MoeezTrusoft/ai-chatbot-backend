from pathlib import Path
from uuid import uuid4

import pytest

from bookcraft.components.portfolio import (
    PortfolioEngine,
    PortfolioRegistry,
    register_portfolio_tools,
)
from bookcraft.infra.cache import CacheKeyBuilder
from bookcraft.tools import (
    IdempotencyStore,
    MemoryAuditSink,
    MemoryCache,
    ToolContext,
    ToolDispatcher,
    ToolRegistry,
    ToolValidationError,
)


def _dispatcher() -> tuple[ToolDispatcher, MemoryAuditSink]:
    registry = ToolRegistry()
    portfolio_registry = PortfolioRegistry.from_files(
        samples_registry_path=Path("data/portfolio/samples.registry.js"),
        genre_hierarchy_path=Path("data/portfolio/genre_hierarchy_links.json"),
        portfolio_docx_path=Path("data/portfolio/portfolio_samples.docx"),
    )
    register_portfolio_tools(registry, PortfolioEngine(portfolio_registry))
    audit = MemoryAuditSink()
    return (
        ToolDispatcher(
            registry=registry,
            idempotency_store=IdempotencyStore(
                client=MemoryCache(),
                keys=CacheKeyBuilder(environment="test"),
                ttl_seconds=60,
            ),
            audit_sink=audit,
        ),
        audit,
    )


def _context(idempotency_key: str = "portfolio-1") -> ToolContext:
    return ToolContext(
        thread_id=uuid4(),
        customer_id=None,
        turn_sequence=1,
        invoked_by="test",
        correlation_id="corr-portfolio",
        idempotency_key=idempotency_key,
        environment="test",
    )


@pytest.mark.asyncio
async def test_portfolio_tool_validates_and_replays_idempotent_call() -> None:
    dispatcher, audit = _dispatcher()
    context = _context()

    first = await dispatcher.invoke(
        tool_name="portfolio.request_samples.v1",
        raw_input={"service": "cover_design_illustration", "genre": "cozy mystery"},
        context=context,
    )
    second = await dispatcher.invoke(
        tool_name="portfolio.request_samples.v1",
        raw_input={"service": "video_trailer"},
        context=context,
    )

    assert first.result == second.result
    assert second.replayed is True
    assert audit.records[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_portfolio_tool_rejects_unknown_fields() -> None:
    dispatcher, audit = _dispatcher()

    with pytest.raises(ToolValidationError):
        await dispatcher.invoke(
            tool_name="portfolio.request_samples.v1",
            raw_input={"service": "cover_design_illustration", "unknown": "blocked"},
            context=_context("portfolio-2"),
        )

    assert audit.records[0]["status"] == "failed"
