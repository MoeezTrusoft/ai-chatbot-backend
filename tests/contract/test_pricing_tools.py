from pathlib import Path
from uuid import uuid4

import pytest

from bookcraft.components.pricing import PricingTimelineEngine
from bookcraft.components.pricing.tools import register_pricing_tools
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
    register_pricing_tools(
        registry,
        PricingTimelineEngine.from_config_dir(Path("data/pricing/v2"), values_approved=True),
    )
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


def _context(idempotency_key: str = "pricing-1") -> ToolContext:
    return ToolContext(
        thread_id=uuid4(),
        customer_id=None,
        turn_sequence=1,
        invoked_by="test",
        correlation_id="corr-1",
        idempotency_key=idempotency_key,
        environment="test",
    )


@pytest.mark.asyncio
async def test_pricing_tool_validates_and_replays_idempotent_call() -> None:
    dispatcher, audit = _dispatcher()
    context = _context()
    payload = {
        "service": "ghostwriting",
        "tier": "standard",
        "word_count": 50000,
        "genre": "fantasy",
        "thread_id": str(context.thread_id),
        "confidence": 0.9,
        "raw_user_request": "quote",
    }

    first = await dispatcher.invoke(
        tool_name="get_pricing_quote.v1",
        raw_input=payload,
        context=context,
    )
    second = await dispatcher.invoke(
        tool_name="get_pricing_quote.v1",
        raw_input={**payload, "word_count": 90000},
        context=context,
    )

    assert first.result == second.result
    assert second.replayed is True
    assert audit.records[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_v2_pricing_tool_returns_engine_quote() -> None:
    dispatcher, audit = _dispatcher()
    context = _context("pricing-v2")

    result = await dispatcher.invoke(
        tool_name="pricing.quote.estimate.v2",
        raw_input={
            "requested_services": ["ghostwriting"],
            "service_inputs": {
                "ghostwriting": {
                    "service_type": "full_ghostwriting",
                    "category": "fiction_standard",
                    "word_count": 60000,
                    "manuscript_status": "outline_ready",
                }
            },
            "global_inputs": {"word_count": 60000},
        },
        context=context,
    )

    assert result.result["status"] == "estimated"
    assert result.result["line_items"]
    assert audit.records[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_pricing_tool_rejects_unknown_fields() -> None:
    dispatcher, audit = _dispatcher()
    context = _context("pricing-2")

    with pytest.raises(ToolValidationError):
        await dispatcher.invoke(
            tool_name="get_pricing_quote.v1",
            raw_input={
                "service": "ghostwriting",
                "thread_id": str(context.thread_id),
                "raw_user_request": "quote",
                "unknown": "blocked",
            },
            context=context,
        )

    assert audit.records[0]["status"] == "failed"
