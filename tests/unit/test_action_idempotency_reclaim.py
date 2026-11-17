"""Durable action idempotency: a duplicate is blocked, a failure can be retried."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from bookcraft.components.actions.dispatcher import _make_idempotency_key
from bookcraft.components.storage.action_idempotency_repository import (
    ActionIdempotencyRepository,
)
from bookcraft.components.storage.db import create_session_factory


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture(params=["memory", "database"])
async def repository(request):
    if request.param == "memory":
        return ActionIdempotencyRepository()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        from sqlmodel import SQLModel

        await connection.run_sync(SQLModel.metadata.create_all)
    return ActionIdempotencyRepository(session_factory=create_session_factory(engine))


async def _claim(repository: ActionIdempotencyRepository, key: str, **kwargs) -> bool:
    return await repository.claim(
        idempotency_key=key, thread_id=uuid4(), action_type="schedule_consultation",
        slots_hash="hash", **kwargs,
    )


@pytest.mark.asyncio
async def test_a_second_claim_is_refused_while_processing_and_after_completion(repository):
    assert await _claim(repository, "k1") is True
    assert await _claim(repository, "k1") is False
    await repository.mark_completed(idempotency_key="k1", result_summary="booked")
    assert await _claim(repository, "k1") is False
    assert await repository.get_status(idempotency_key="k1") == "completed"


@pytest.mark.asyncio
async def test_a_failed_action_can_be_claimed_again(repository):
    assert await _claim(repository, "k2") is True
    await repository.mark_failed(idempotency_key="k2", error_code="requested_time_in_past")
    assert await _claim(repository, "k2") is True
    assert await repository.get_status(idempotency_key="k2") == "processing"
    # ...but only once: the retry itself is protected again.
    assert await _claim(repository, "k2") is False


@pytest.mark.asyncio
async def test_an_expired_claim_no_longer_blocks(repository):
    assert await _claim(repository, "k3", expires_at=_now() - timedelta(minutes=1)) is True
    await repository.mark_completed(idempotency_key="k3", result_summary="booked")
    assert await _claim(repository, "k3", expires_at=_now() + timedelta(hours=24)) is True
    assert await _claim(repository, "k3", expires_at=_now() + timedelta(hours=24)) is False


def test_the_requested_time_is_part_of_the_key():
    thread_id = uuid4()
    slots = {"name": "Dana", "phone": "5552013344", "requested_time_text": "Friday at 2pm"}
    same = _make_idempotency_key(thread_id, "schedule_consultation", dict(slots))
    assert same == _make_idempotency_key(thread_id, "schedule_consultation", dict(slots))
    moved = {**slots, "requested_time_text": "Monday at 11am"}
    assert same != _make_idempotency_key(thread_id, "schedule_consultation", moved)
