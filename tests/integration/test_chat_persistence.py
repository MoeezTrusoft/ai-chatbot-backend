from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from bookcraft.api.chat import ChatTurnRequest, ChatTurnResponse
from bookcraft.api.main import build_chat_service
from bookcraft.components.storage.db import create_session_factory
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.infra.config import get_settings


@pytest.fixture
async def db_session_factory():
    """Create an in-memory SQLite session factory for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        from sqlmodel import SQLModel

        await connection.run_sync(SQLModel.metadata.create_all)
    return create_session_factory(engine)


@pytest.fixture
async def chat_service_with_persistence(db_session_factory):
    """Create a ChatService with thread persistence enabled."""
    settings = get_settings()
    thread_repository = ThreadRepository(session_factory=db_session_factory)
    service = build_chat_service(settings, thread_repository=thread_repository)
    return service


@pytest.mark.asyncio
async def test_chat_persistence_across_instances(db_session_factory):
    """Test that thread state persists across new ChatService instances."""
    settings = get_settings()
    thread_repository = ThreadRepository(session_factory=db_session_factory)
    thread_id = uuid4()

    service1 = build_chat_service(settings, thread_repository=thread_repository)
    request1 = ChatTurnRequest(
        thread_id=thread_id,
        message="What is your word count?",
    )

    response1: ChatTurnResponse = await service1.handle_turn(request1)
    assert response1.thread_id == thread_id

    service2 = build_chat_service(settings, thread_repository=thread_repository)
    request2 = ChatTurnRequest(
        thread_id=thread_id,
        message="About 50000 words",
    )

    response2: ChatTurnResponse = await service2.handle_turn(request2)
    assert response2.thread_id == thread_id

    assert len(response2.bubbles) > 0
