"""Integration test: tool audit records persist to a SQLite DB via DbToolAuditSink."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlmodel import col, select

from bookcraft.api.main import build_chat_service
from bookcraft.components.pricing.models import PricingQuoteRequest
from bookcraft.components.storage.db import create_all, create_engine, create_session_factory
from bookcraft.components.storage.models import ToolInvocationLog
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.infra.config import Settings
from bookcraft.tools.schemas import ToolContext


@pytest.mark.asyncio
async def test_pricing_tool_audit_persists_to_db(tmp_path: pytest.TempPathFactory) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path}/audit.db"
    settings = Settings(
        app_env="integration",
        database_url=database_url,
        pricing_v2_values_approved=False,
    )

    engine = create_engine(settings, database_url=database_url)
    await create_all(engine)
    session_factory = create_session_factory(engine)

    service = build_chat_service(
        settings,
        thread_repository=ThreadRepository(session_factory=session_factory),
        session_factory=session_factory,
    )

    request = PricingQuoteRequest.model_validate(
        {
            "thread_id": str(uuid4()),
            "customer_id": None,
            "requested_services": ["ghostwriting"],
            "service_inputs": {
                "ghostwriting": {
                    "service_type": "full_ghostwriting",
                    "category": "fiction_standard",
                    "word_count": 50000,
                    "manuscript_status": "outline_ready",
                }
            },
            "global_inputs": {
                "genre": "fantasy",
                "word_count": 50000,
                "page_count": None,
                "manuscript_status": "outline_ready",
            },
            "field_meta_snapshot": {},
        }
    )

    assert service.tool_dispatcher is not None

    await service.tool_dispatcher.invoke(
        tool_name="pricing.quote.estimate.v2",
        raw_input=request.model_dump(mode="json"),
        context=ToolContext(
            thread_id=uuid4(),
            customer_id=None,
            turn_sequence=1,
            invoked_by="test",
            correlation_id="db-tool-audit-test",
            idempotency_key="db-tool-audit-test-key",
            environment="integration",
        ),
    )

    async with session_factory() as session:
        result = await session.execute(
            select(ToolInvocationLog).where(
                col(ToolInvocationLog.correlation_id) == "db-tool-audit-test"
            )
        )
        row = result.scalar_one_or_none()

    await engine.dispose()

    assert row is not None, "Expected a ToolInvocationLog row but found none"
    assert row.tool_name == "pricing.quote.estimate.v2"
    assert row.status == "succeeded"
    assert row.params_hash
    assert row.duration_ms is not None
