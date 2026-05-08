from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from bookcraft.components.storage.db import create_session_factory
from bookcraft.components.storage.thread_repository import (
    ThreadRepository,
    ThreadVersionConflictError,
)
from bookcraft.domain.state import ThreadState


@pytest.fixture
async def db_session_factory():
    """Create an in-memory SQLite session factory for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        from sqlmodel import SQLModel

        await connection.run_sync(SQLModel.metadata.create_all)
    return create_session_factory(engine)


@pytest.mark.asyncio
async def test_load_or_create_creates_new_thread(db_session_factory):
    """Test that load_or_create creates a new thread when it doesn't exist."""
    repo = ThreadRepository(session_factory=db_session_factory)
    thread_id = uuid4()

    result = await repo.load_or_create(thread_id=thread_id, customer_id=None)

    assert result.thread_id == thread_id
    assert result.version == 0
    assert result.turn_count == 0
    assert result.event_count == 0
    assert result.last_event_hash is None
    assert isinstance(result.state, ThreadState)


@pytest.mark.asyncio
async def test_load_or_create_returns_same_state(db_session_factory):
    """Test that second load returns the same state."""
    repo = ThreadRepository(session_factory=db_session_factory)
    thread_id = uuid4()

    first = await repo.load_or_create(thread_id=thread_id, customer_id=None)
    second = await repo.load_or_create(thread_id=thread_id, customer_id=None)

    assert first.thread_id == second.thread_id
    assert first.version == second.version
    assert first.turn_count == second.turn_count


@pytest.mark.asyncio
async def test_append_event_creates_hash_chained_event(db_session_factory):
    """Test that append_event creates a hash-chained event."""
    repo = ThreadRepository(session_factory=db_session_factory)
    thread_id = uuid4()

    hash1 = await repo.append_event(
        thread_id=thread_id,
        sequence=1,
        event_type="user.message",
        payload={"text": "hello"},
        previous_hash=None,
    )

    hash2 = await repo.append_event(
        thread_id=thread_id,
        sequence=2,
        event_type="assistant.response",
        payload={"text": "hi"},
        previous_hash=hash1,
    )

    assert hash1 != hash2
    assert len(hash1) == 64  # SHA256 hex


@pytest.mark.asyncio
async def test_save_state_increments_version(db_session_factory):
    """Test that save_state increments version."""
    repo = ThreadRepository(session_factory=db_session_factory)
    thread_id = uuid4()
    customer_id = uuid4()

    loaded = await repo.load_or_create(thread_id=thread_id, customer_id=customer_id)
    assert loaded.version == 0

    new_state = ThreadState()
    new_version = await repo.save_state(
        thread_id=thread_id,
        state=new_state,
        expected_version=0,
        language="en",
    )

    assert new_version == 1

    reloaded = await repo.load_or_create(thread_id=thread_id, customer_id=customer_id)
    assert reloaded.version == 1


@pytest.mark.asyncio
async def test_save_state_raises_on_version_conflict(db_session_factory):
    """Test that stale expected_version raises ThreadVersionConflictError."""
    repo = ThreadRepository(session_factory=db_session_factory)
    thread_id = uuid4()

    await repo.load_or_create(thread_id=thread_id, customer_id=None)
    new_state = ThreadState()

    # Save once to increment version
    await repo.save_state(
        thread_id=thread_id,
        state=new_state,
        expected_version=0,
        language="en",
    )

    # Try to save with stale expected_version
    with pytest.raises(ThreadVersionConflictError):
        await repo.save_state(
            thread_id=thread_id,
            state=new_state,
            expected_version=0,  # Already incremented to 1
            language="en",
        )
