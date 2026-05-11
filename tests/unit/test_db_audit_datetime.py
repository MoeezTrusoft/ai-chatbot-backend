from uuid import uuid4

import pytest
from sqlmodel import select

from bookcraft.components.storage.db import create_all, create_engine, create_session_factory
from bookcraft.components.storage.models import ToolInvocationLog
from bookcraft.domain.enums import ToolInvocationStatus
from bookcraft.infra.config import Settings
from bookcraft.tools.db_audit import DbToolAuditSink
from bookcraft.tools.schemas import ToolContext


@pytest.mark.asyncio
async def test_db_tool_audit_completed_at_is_naive_utc(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path}/audit.db"
    settings = Settings(app_env="integration", database_url=database_url)
    engine = create_engine(settings, database_url=database_url)
    await create_all(engine)
    session_factory = create_session_factory(engine)

    sink = DbToolAuditSink(session_factory=session_factory)
    await sink.record(
        context=ToolContext(
            thread_id=uuid4(),
            customer_id=None,
            turn_sequence=1,
            invoked_by="test",
            correlation_id="audit-datetime-test",
            idempotency_key="audit-datetime-test-key",
            environment="test",
        ),
        tool_name="portfolio.request_samples.v1",
        params_hash="abc",
        params={"service": "ghostwriting"},
        status=ToolInvocationStatus.SUCCEEDED,
        result={"status": "unavailable_confidential"},
        duration_ms=1,
    )

    async with session_factory() as session:
        result = await session.execute(select(ToolInvocationLog))
        row = result.scalar_one()

    await engine.dispose()

    assert row.completed_at is not None
    assert row.completed_at.tzinfo is None
