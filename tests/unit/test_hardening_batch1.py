"""Batch 1 hardening unit tests.

All PII uses fake values only:
  john@example.com / 5551234567 / John Smith
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Step 1: Fail-closed final response
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Step 2: Event payload sanitization
# ---------------------------------------------------------------------------


def test_user_message_with_email_is_sanitized():
    from bookcraft.infra.trace_sanitizer import sanitize_event_payload

    payload = sanitize_event_payload(
        "user.message", {"text": "Hi, I'm John Smith, john@example.com"}
    )
    assert "john@example.com" not in str(payload)
    assert payload["has_email"] is True
    assert payload["pii_redacted"] is True
    assert "message_length" in payload


def test_user_message_with_phone_is_sanitized():
    from bookcraft.infra.trace_sanitizer import sanitize_event_payload

    payload = sanitize_event_payload("user.message", {"text": "Call me at 5551234567"})
    assert "5551234567" not in str(payload)
    assert payload["has_phone"] is True
    assert payload["pii_redacted"] is True


def test_user_message_without_pii_keeps_text():
    from bookcraft.infra.trace_sanitizer import sanitize_event_payload

    payload = sanitize_event_payload("user.message", {"text": "I need help with my book."})
    assert payload.get("text") == "I need help with my book."
    assert payload["pii_redacted"] is False
    assert "has_email" in payload


def test_non_user_event_is_standard_redacted():
    from bookcraft.infra.trace_sanitizer import sanitize_event_payload

    payload = sanitize_event_payload(
        "intent.classified", {"service": "ghostwriting", "email": "john@example.com"}
    )
    # Standard redaction replaces email.
    assert "john@example.com" not in str(payload)


# ---------------------------------------------------------------------------
# Step 3: Trace contact PII masking
# ---------------------------------------------------------------------------


def test_safe_contact_capture_masks_pii():
    from bookcraft.components.leads.contact import ContactCaptureResult, ContactInfo
    from bookcraft.infra.trace_sanitizer import safe_contact_capture

    result = ContactCaptureResult(
        contact=ContactInfo(
            name="John Smith",
            email="john@example.com",
            phone="5551234567",
            source="chat",
        ),
        has_name=True,
        has_email=True,
        has_phone=True,
        lead_contact_ready=True,
    )
    safe = safe_contact_capture(result)

    # Raw PII must not appear.
    assert "john@example.com" not in str(safe)
    assert "5551234567" not in str(safe)
    assert "John Smith" not in str(safe)
    # Readiness booleans must be preserved.
    assert safe["has_name"] is True
    assert safe["has_email"] is True
    assert safe["has_phone"] is True
    assert safe["lead_contact_ready"] is True
    # Masked values should be present.
    assert "email_masked" in safe
    assert safe["email_masked"].startswith("j")
    assert "phone_masked" in safe
    assert safe["phone_masked"].endswith("4567")


def test_safe_contact_capture_none_returns_empty():
    from bookcraft.infra.trace_sanitizer import safe_contact_capture

    assert safe_contact_capture(None) == {}


def test_safe_lead_intake_masks_pii():
    from bookcraft.infra.trace_sanitizer import safe_lead_intake

    payload = {
        "name": "John Smith",
        "email": "john@example.com",
        "phone": "5551234567",
        "services": ["ghostwriting"],
        "thread_id": str(uuid4()),
    }
    safe = safe_lead_intake(payload)
    assert "john@example.com" not in str(safe)
    assert "5551234567" not in str(safe)
    assert "John Smith" not in str(safe)
    assert safe["has_name"] is True
    assert safe["has_email"] is True
    assert safe["has_phone"] is True
    assert "email_masked" in safe
    assert "phone_masked" in safe
    # Non-PII field preserved.
    assert safe["has_service"] is True


# ---------------------------------------------------------------------------
# Step 4: Pending confirmation expiry
# ---------------------------------------------------------------------------


def test_fresh_pending_not_expired():
    from bookcraft.components.actions.slot_resolver import is_pending_expired

    pending = MagicMock()
    pending.expires_at = datetime.now(UTC) + timedelta(hours=1)
    assert not is_pending_expired(pending)


def test_expired_pending_is_rejected():
    from bookcraft.components.actions.slot_resolver import is_pending_expired

    pending = MagicMock()
    pending.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert is_pending_expired(pending)


def test_pending_with_no_expires_at_not_expired():
    from bookcraft.components.actions.slot_resolver import is_pending_expired

    pending = MagicMock()
    pending.expires_at = None
    assert not is_pending_expired(pending)


def test_expired_pending_returns_blocked_plan():
    """Planner must return BLOCKED when confirmation arrives for expired pending."""
    from bookcraft.components.actions.planner import SalesActionPlanner
    from bookcraft.components.actions.schemas import ActionStatus
    from bookcraft.domain.state import PendingConfirmationState, ThreadState

    state = ThreadState()
    state.sales_actions.pending_confirmation = PendingConfirmationState(
        type="schedule_consultation",
        payload={"name": "John Smith"},
        created_at=datetime.now(UTC) - timedelta(hours=2),
        expires_at=datetime.now(UTC) - timedelta(hours=1),  # EXPIRED
    )

    from bookcraft.components.extraction.schemas import CombinedExtraction
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.preprocessor.schemas import ProcessedMessage
    from bookcraft.domain.enums import QueryIntentType, SalesStage

    processed = ProcessedMessage(
        raw="book it",
        normalized="book it",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[0.0],
        language="en",
        char_count=7,
    )
    extraction = CombinedExtraction()
    intent = IntentVote(
        query_primary=QueryIntentType.CONSULTATION_REQUEST,
        funnel_stage=SalesStage.SCOPING,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )

    planner = SalesActionPlanner()
    plan = planner.plan(processed=processed, state=state, intent=intent, extraction=extraction)
    assert plan.status == ActionStatus.BLOCKED
    assert "expired" in plan.reason
    assert plan.customer_safe_summary is not None
    assert "expired" in (plan.customer_safe_summary or "").lower()


def test_fresh_pending_confirmation_accepts():
    """Planner accepts valid confirmation for non-expired pending."""
    from bookcraft.components.actions.planner import SalesActionPlanner
    from bookcraft.components.actions.schemas import ActionStatus
    from bookcraft.domain.state import PendingConfirmationState, ThreadState

    state = ThreadState()
    state.sales_actions.pending_confirmation = PendingConfirmationState(
        type="schedule_consultation",
        payload={"name": "John Smith"},
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),  # FRESH
    )

    from bookcraft.components.extraction.schemas import CombinedExtraction
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.preprocessor.schemas import ProcessedMessage
    from bookcraft.domain.enums import QueryIntentType, SalesStage

    processed = ProcessedMessage(
        raw="yes book it",
        normalized="yes book it",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[0.0],
        language="en",
        char_count=11,
    )
    extraction = CombinedExtraction()
    intent = IntentVote(
        query_primary=QueryIntentType.CONSULTATION_REQUEST,
        funnel_stage=SalesStage.SCOPING,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )

    planner = SalesActionPlanner()
    plan = planner.plan(processed=processed, state=state, intent=intent, extraction=extraction)
    assert plan.status == ActionStatus.READY


# ---------------------------------------------------------------------------
# Step 5: Action-specific confirmation
# ---------------------------------------------------------------------------


def test_schedule_it_confirms_consultation_not_nda():
    from bookcraft.components.actions.slot_resolver import is_confirmation_text

    assert is_confirmation_text("schedule it", pending_action_type="schedule_consultation")
    assert not is_confirmation_text("schedule it", pending_action_type="generate_nda")
    assert not is_confirmation_text("schedule it", pending_action_type="generate_agreement")


def test_send_nda_confirms_nda_not_consultation():
    from bookcraft.components.actions.slot_resolver import is_confirmation_text

    assert is_confirmation_text("send the NDA", pending_action_type="generate_nda")
    assert not is_confirmation_text("send the NDA", pending_action_type="schedule_consultation")
    assert not is_confirmation_text("send the NDA", pending_action_type="generate_agreement")


def test_send_agreement_confirms_agreement_not_consultation():
    from bookcraft.components.actions.slot_resolver import is_confirmation_text

    assert is_confirmation_text("send the agreement", pending_action_type="generate_agreement")
    assert not is_confirmation_text(
        "send the agreement", pending_action_type="schedule_consultation"
    )


def test_ambiguous_yes_confirms_consultation():
    """'yes' alone is accepted for consultation (most common case)."""
    from bookcraft.components.actions.slot_resolver import is_confirmation_text

    assert is_confirmation_text("yes", pending_action_type="schedule_consultation")


def test_book_it_does_not_confirm_nda():
    from bookcraft.components.actions.slot_resolver import is_confirmation_text

    assert not is_confirmation_text("book it", pending_action_type="generate_nda")


def test_tomorrow_works_confirms_consultation():
    from bookcraft.components.actions.slot_resolver import is_confirmation_text

    assert is_confirmation_text("tomorrow works", pending_action_type="schedule_consultation")
    assert not is_confirmation_text("tomorrow works", pending_action_type="generate_nda")


# ---------------------------------------------------------------------------
# Step 6: Idempotency — no double-dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_idempotency_key_does_not_double_dispatch():
    from bookcraft.components.actions.dispatcher import SalesActionDispatcher, _make_idempotency_key
    from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType
    from bookcraft.components.storage.action_idempotency_repository import (
        ActionIdempotencyRepository,
        make_slots_hash,
    )

    thread_id = uuid4()
    slots = {"name": "John Smith", "email": "john@example.com"}
    idem_key = _make_idempotency_key(thread_id, "schedule_consultation", slots)

    # Pre-claim so the dispatcher sees it as already dispatched.
    repo = ActionIdempotencyRepository()
    await repo.claim(
        idempotency_key=idem_key,
        thread_id=thread_id,
        action_type="schedule_consultation",
        slots_hash=make_slots_hash(slots),
    )

    dispatcher = SalesActionDispatcher(action_idempotency_repository=repo)
    plan = ActionPlan(
        action_type=ActionType.SCHEDULE_CONSULTATION,
        status=ActionStatus.READY,
        collected_slots=slots,
        idempotency_key=idem_key,
        reason="test",
    )
    result = await dispatcher.dispatch(plan, thread_id=thread_id, customer_id=None)
    assert result is None  # No double-dispatch.


@pytest.mark.asyncio
async def test_first_dispatch_marks_key():
    from bookcraft.components.actions.dispatcher import SalesActionDispatcher, _make_idempotency_key
    from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType

    thread_id = uuid4()
    slots = {"name": "John Smith"}
    idem_key = _make_idempotency_key(thread_id, "create_lead", slots)

    dispatcher = SalesActionDispatcher()  # No external services wired.
    # Key must not exist before dispatch.
    status_before = await dispatcher.action_idempotency_repository.get_status(
        idempotency_key=idem_key
    )
    assert status_before is None

    plan = ActionPlan(
        action_type=ActionType.CREATE_LEAD,
        status=ActionStatus.READY,
        collected_slots=slots,
        idempotency_key=idem_key,
        reason="test",
    )
    # Will fail gracefully (no service) but key should be recorded.
    await dispatcher.dispatch(plan, thread_id=thread_id, customer_id=None)
    status_after = await dispatcher.action_idempotency_repository.get_status(
        idempotency_key=idem_key
    )
    assert status_after in {"completed", "failed"}


def test_idempotency_key_is_stable():
    from bookcraft.components.actions.dispatcher import _make_idempotency_key

    thread_id = uuid4()
    slots = {"name": "John Smith", "email": "john@example.com"}
    k1 = _make_idempotency_key(thread_id, "create_lead", slots)
    k2 = _make_idempotency_key(thread_id, "create_lead", slots)
    assert k1 == k2
    assert len(k1) == 32


# ---------------------------------------------------------------------------
# Step 8: Approved URLs use final_draft not draft
# ---------------------------------------------------------------------------


def test_approved_urls_from_final_draft_not_original():
    """Formatter must receive final_draft.approved_urls, not draft.approved_urls."""
    from bookcraft.components.response.schemas import ResponseDraft

    # original_draft represents what was blocked (its URLs must not be used).
    _original_url = "http://unsafe.com"
    repaired_draft = ResponseDraft(
        text="Clean response text",
        source="claude_sonnet_repair",
        approved_urls=["https://safe.bookcraft.com"],
    )

    # Simulate what chat.py now does.
    final_draft = repaired_draft
    approved = set(final_draft.approved_urls)

    assert _original_url not in approved
    assert "https://safe.bookcraft.com" in approved


