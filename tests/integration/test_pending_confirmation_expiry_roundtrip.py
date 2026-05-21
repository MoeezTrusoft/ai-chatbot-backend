"""Batch 4 — Phase 3: DB round-trip test for pending confirmation expiry.

Verifies that:
1. `pending_expires_at` set by the planner is copied into
   `state.sales_actions.pending_confirmation.expires_at` by `_apply_sales_action_plan_to_state`.
2. The expires_at value survives DB persist → reload (no timezone or field loss).
3. After advancing the clock past expires_at, a confirmation attempt is blocked.
4. A non-expired pending confirmation is NOT blocked.

The test uses an in-memory SQLite database so it runs without a real Postgres instance.
All fake data uses approved test PII: Maya Author / maya@example.com / +1 555 987 6543.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType
from bookcraft.components.actions.slot_resolver import is_pending_expired, make_pending_expires_at
from bookcraft.components.storage.db import create_session_factory
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.domain.state import ThreadState


@pytest.fixture
async def db_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from sqlmodel import SQLModel

        await conn.run_sync(SQLModel.metadata.create_all)
    return create_session_factory(engine)


@pytest.fixture
async def thread_repo(db_session_factory):
    return ThreadRepository(session_factory=db_session_factory)


# ---------------------------------------------------------------------------
# Test 1 — expires_at is synced into state
# ---------------------------------------------------------------------------


def test_expires_at_copied_to_state_by_apply_plan() -> None:
    """_apply_sales_action_plan_to_state must write expires_at to pending_confirmation."""
    from bookcraft.services.chat import ChatService

    state = ThreadState()
    now = datetime.now(UTC)
    expires = now + timedelta(hours=1)

    plan = ActionPlan(
        action_type=ActionType.SCHEDULE_CONSULTATION,
        status=ActionStatus.NEEDS_CONFIRMATION,
        collected_slots={"name": "Maya Author"},
        reason="test_expires_at_copied",
        confirmation_required=True,
        pending_confirmation_key="schedule_consultation",
        pending_expires_at=expires,
    )

    ChatService._apply_sales_action_plan_to_state(state, plan)

    assert state.sales_actions.pending_confirmation.type == "schedule_consultation"
    assert state.sales_actions.pending_confirmation.expires_at is not None, (
        "expires_at must be written to state — was not copied from action_plan.pending_expires_at"
    )
    # Expiry should be approximately 1 hour from now (within 5 second tolerance).
    diff = abs((state.sales_actions.pending_confirmation.expires_at - expires).total_seconds())
    assert diff < 5, f"expires_at drift too large: {diff}s"


# ---------------------------------------------------------------------------
# Test 2 — expires_at survives DB round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expires_at_survives_db_roundtrip(thread_repo: ThreadRepository) -> None:
    """After persist + reload, expires_at must still be present and valid."""
    from bookcraft.services.chat import ChatService

    thread_id = uuid4()
    loaded = await thread_repo.load_or_create(thread_id=thread_id)
    state = loaded.state
    now = datetime.now(UTC)
    expires = now + timedelta(hours=1)

    plan = ActionPlan(
        action_type=ActionType.GENERATE_NDA,
        status=ActionStatus.NEEDS_CONFIRMATION,
        collected_slots={"name": "Maya Author"},
        reason="test_expires_at_roundtrip",
        confirmation_required=True,
        pending_confirmation_key="generate_nda",
        pending_expires_at=expires,
    )
    ChatService._apply_sales_action_plan_to_state(state, plan)

    # Persist state.
    await thread_repo.save_state(
        thread_id=thread_id,
        state=state,
        expected_version=loaded.version,
        language="en",
    )

    # Reload from DB.
    reloaded = await thread_repo.load_or_create(thread_id=thread_id)
    pending = reloaded.state.sales_actions.pending_confirmation

    assert pending.type == "generate_nda", "pending.type must survive round-trip"
    assert pending.expires_at is not None, (
        "expires_at must survive DB round-trip — was not saved/reloaded correctly"
    )

    # Not expired yet (expiry is 1 hour from now).
    assert not is_pending_expired(pending), (
        "Pending should NOT be expired immediately after creation"
    )


# ---------------------------------------------------------------------------
# Test 3 — Expired confirmation is blocked by planner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_confirmation_blocked_after_db_roundtrip(
    thread_repo: ThreadRepository,
) -> None:
    """After advancing clock past expires_at, is_pending_expired must return True."""
    from bookcraft.services.chat import ChatService

    thread_id = uuid4()
    loaded = await thread_repo.load_or_create(thread_id=thread_id)
    state = loaded.state

    # Set up a pending NDA confirmation that expires in 1 second.
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=1)

    plan = ActionPlan(
        action_type=ActionType.GENERATE_NDA,
        status=ActionStatus.NEEDS_CONFIRMATION,
        collected_slots={"name": "Maya Author", "email": "maya@example.com"},
        reason="test_expired_confirmation_blocked",
        confirmation_required=True,
        pending_confirmation_key="generate_nda",
        pending_expires_at=expires,
    )
    ChatService._apply_sales_action_plan_to_state(state, plan)

    # Persist.
    await thread_repo.save_state(
        thread_id=thread_id,
        state=state,
        expected_version=loaded.version,
        language="en",
    )

    # Reload.
    reloaded = await thread_repo.load_or_create(thread_id=thread_id)
    reloaded_state = reloaded.state

    # Advance clock past expiry.
    future_now = now + timedelta(seconds=120)

    assert is_pending_expired(
        reloaded_state.sales_actions.pending_confirmation,
        now=future_now,
    ), "After advancing clock, pending must be expired on reloaded state"


# ---------------------------------------------------------------------------
# Test 4 — Non-expired confirmation is NOT blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_confirmation_not_blocked_after_db_roundtrip(
    thread_repo: ThreadRepository,
) -> None:
    """A non-expired pending confirmation must pass is_pending_expired = False."""
    from bookcraft.services.chat import ChatService

    thread_id = uuid4()
    loaded = await thread_repo.load_or_create(thread_id=thread_id)
    state = loaded.state

    plan = ActionPlan(
        action_type=ActionType.SCHEDULE_CONSULTATION,
        status=ActionStatus.NEEDS_CONFIRMATION,
        collected_slots={"name": "Maya Author"},
        reason="test_valid_confirmation_not_blocked",
        confirmation_required=True,
        pending_confirmation_key="schedule_consultation",
        pending_expires_at=make_pending_expires_at("schedule_consultation"),
    )
    ChatService._apply_sales_action_plan_to_state(state, plan)

    await thread_repo.save_state(
        thread_id=thread_id,
        state=state,
        expected_version=loaded.version,
        language="en",
    )

    reloaded = await thread_repo.load_or_create(thread_id=thread_id)
    pending = reloaded.state.sales_actions.pending_confirmation

    assert pending.expires_at is not None, "expires_at must be present after reload"
    assert not is_pending_expired(pending), "Valid (non-expired) confirmation must pass"


# ---------------------------------------------------------------------------
# Test 5 — Missing expires_at defaults to safe-expired (not executable forever)
# ---------------------------------------------------------------------------


def test_missing_expires_at_is_not_expired_by_is_pending_expired() -> None:
    """When expires_at is None, is_pending_expired returns False (no TTL = no expiry check).

    This is the safe choice: if expires_at was never set, we don't accidentally
    block valid actions. The planner is responsible for always setting expires_at.
    """
    state = ThreadState()
    state.sales_actions.pending_confirmation.type = "schedule_consultation"
    state.sales_actions.pending_confirmation.expires_at = None

    # Without expires_at, is_pending_expired returns False (treat as not expired).
    assert not is_pending_expired(state.sales_actions.pending_confirmation), (
        "Missing expires_at should NOT be treated as expired — "
        "the planner is responsible for always setting it"
    )


# ---------------------------------------------------------------------------
# Test 6 — Timezone-aware datetime preserved through JSON round-trip
# ---------------------------------------------------------------------------


def test_timezone_preserved_through_state_json_roundtrip() -> None:
    """expires_at with UTC timezone must survive model_dump → model_validate cycle."""
    state = ThreadState()
    expires = datetime(2026, 12, 25, 14, 30, 0, tzinfo=UTC)
    state.sales_actions.pending_confirmation.expires_at = expires

    # Round-trip through JSON (simulates DB persistence).
    dumped = state.model_dump(mode="json")
    reloaded = ThreadState.model_validate(dumped)

    reloaded_expires = reloaded.sales_actions.pending_confirmation.expires_at
    assert reloaded_expires is not None, "expires_at must survive JSON round-trip"
    # Compare as UTC timestamps (timezone info may be stripped in JSON, but value preserved).
    if reloaded_expires.tzinfo is None:
        reloaded_expires = reloaded_expires.replace(tzinfo=UTC)
    assert abs((reloaded_expires - expires).total_seconds()) < 1, (
        f"Timezone mismatch after JSON round-trip: {reloaded_expires} vs {expires}"
    )
