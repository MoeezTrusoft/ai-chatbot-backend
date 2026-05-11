from uuid import uuid4

import pytest
from sqlmodel import col, select

from bookcraft.components.storage.db import create_all, create_engine, create_session_factory
from bookcraft.components.storage.models import ThreadRecord
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings


@pytest.mark.asyncio
async def test_thread_repository_redacts_pii_before_persisting_state(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path}/thread-state.db"
    settings = Settings(app_env="integration", database_url=database_url)
    engine = create_engine(settings, database_url=database_url)
    await create_all(engine)
    session_factory = create_session_factory(engine)
    repository = ThreadRepository(session_factory=session_factory)

    loaded = await repository.load_or_create(thread_id=uuid4())
    state = ThreadState()
    state.personal.name = FieldMeta[str](
        value="Avery Author",
        confidence=0.99,
        source="user_stated",
        raw_excerpt="My name is Avery Author",
    )
    state.personal.email = FieldMeta[str](
        value="avery@example.com",
        confidence=0.99,
        source="user_stated",
        raw_excerpt="avery@example.com",
    )
    state.personal.phone = FieldMeta[str](
        value="+1 555-123-4567",
        confidence=0.99,
        source="user_stated",
        raw_excerpt="+1 555-123-4567",
    )
    state.project.synopsis = FieldMeta[str](
        value="Private synopsis. Email author@example.com.",
        confidence=0.9,
        source="user_stated",
        raw_excerpt="Private synopsis from author@example.com",
    )
    state.rolling_summary = "Call +1 555-123-4567 or email avery@example.com"

    await repository.save_state(
        thread_id=loaded.thread_id,
        state=state,
        expected_version=loaded.version,
        language="en",
    )

    async with session_factory() as session:
        result = await session.execute(
            select(ThreadRecord).where(col(ThreadRecord.id) == loaded.thread_id)
        )
        row = result.scalar_one()

    await engine.dispose()

    serialized = str(row.state)
    assert "Avery Author" not in serialized
    assert "avery@example.com" not in serialized
    assert "author@example.com" not in serialized
    assert "+1 555-123-4567" not in serialized
    assert row.state["personal"]["name"]["value"] == "[REDACTED_NAME]"
    assert row.state["personal"]["email"]["value"] == "[REDACTED_EMAIL]"
    assert row.state["personal"]["phone"]["value"] == "[REDACTED_PHONE]"
    assert "[REDACTED_EMAIL]" in serialized
    assert "[REDACTED_PHONE]" in serialized
