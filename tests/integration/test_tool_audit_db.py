"""Integration test: tool audit records persist to a SQLite DB via DbToolAuditSink."""
from __future__ import annotations

import pytest
from sqlmodel import col, select

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service
from bookcraft.components.storage.db import create_all, create_engine, create_session_factory
from bookcraft.components.storage.models import ToolInvocationLog
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.infra.config import Settings


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

    await service.handle_turn(
        ChatTurnRequest(
            message="How much does ghostwriting cost for a 50000 word fantasy novel?",
            correlation_id="db-tool-audit-test",
        )
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
