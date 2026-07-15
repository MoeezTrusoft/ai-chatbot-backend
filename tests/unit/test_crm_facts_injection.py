"""Tests for the CRM facts injection feature.

Covers:
  - ChatFactsRequest schema validation
  - handle_inject_facts() applies fields to thread state correctly
  - Confidence gating: only fills empty or lower-confidence fields
  - pack_builder: name/email/phone in known_facts after injection
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from bookcraft.api.chat import ChatFactsRequest, ChatFactsResponse
from bookcraft.components.context import ContextPackBuilder
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.extraction.state_applier import StateApplier
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, SalesStage, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _field(value, *, confidence: float = 0.92):
    return FieldMeta(value=value, confidence=confidence, source=Source.AI_EXTRACTED)


def _intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=None,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.90,
        rationale="test",
        evidence=[],
    )


def _apply_facts(state: ThreadState, *, name=None, email=None, phone=None) -> ThreadState:
    """Simulate handle_inject_facts: build deltas and apply them."""
    applier = StateApplier()
    deltas = []
    for path, value in [
        ("personal.name", name),
        ("personal.email", email),
        ("personal.phone", phone),
    ]:
        if value and isinstance(value, str) and value.strip():
            deltas.append(StateDelta(
                path=path,
                value=value.strip(),
                confidence=0.98,
                source=Source.AI_EXTRACTED,
                extracted_by="crm_sync.signup_form",
            ))
    if deltas:
        state = applier.apply(state, CombinedExtraction(state_deltas=deltas))
    return state


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestChatFactsRequestSchema:
    def test_requires_thread_id(self):
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            ChatFactsRequest(thread_id=None, name="Alice")  # type: ignore[arg-type]

    def test_all_contact_fields_optional(self):
        req = ChatFactsRequest(thread_id=uuid4())
        assert req.name is None
        assert req.email is None
        assert req.phone is None

    def test_name_accepted(self):
        req = ChatFactsRequest(thread_id=uuid4(), name="Gina Author")
        assert req.name == "Gina Author"

    def test_email_accepted(self):
        req = ChatFactsRequest(thread_id=uuid4(), email="gina@example.com")
        assert req.email == "gina@example.com"

    def test_phone_accepted(self):
        req = ChatFactsRequest(thread_id=uuid4(), phone="815-997-0607")
        assert req.phone == "815-997-0607"

    def test_default_source_label(self):
        req = ChatFactsRequest(thread_id=uuid4())
        assert req.source_label == "crm_sync"

    def test_custom_source_label(self):
        req = ChatFactsRequest(thread_id=uuid4(), source_label="signup_form")
        assert req.source_label == "signup_form"

    def test_fields_applied_in_response(self):
        resp = ChatFactsResponse(thread_id=uuid4(), fields_applied=["name", "email"])
        assert "name" in resp.fields_applied
        assert "email" in resp.fields_applied


# ---------------------------------------------------------------------------
# Confidence gating: fill-only and overwrite logic
# ---------------------------------------------------------------------------

class TestConfidenceGating:
    """Verify that the 0.98 confidence correctly fills empty fields and overwrites
    lower-confidence extractions without touching higher-confidence facts."""

    def test_fills_empty_name(self):
        state = ThreadState()  # name.value is None
        state = _apply_facts(state, name="Gina Author")
        assert state.personal.name.value == "Gina Author"

    def test_fills_empty_email(self):
        state = ThreadState()
        state = _apply_facts(state, email="gina@example.com")
        assert state.personal.email.value == "gina@example.com"

    def test_fills_empty_phone(self):
        state = ThreadState()
        state = _apply_facts(state, phone="815-997-0607")
        assert state.personal.phone.value == "815-997-0607"

    def test_overwrites_lower_confidence_name(self):
        """CRM data (0.98) overwrites a low-confidence AI extraction (0.70)."""
        state = ThreadState()
        state.personal.name = _field("G. Author", confidence=0.70)
        state = _apply_facts(state, name="Gina Author")
        assert state.personal.name.value == "Gina Author"

    def test_does_not_overwrite_higher_confidence_name(self):
        """CRM data (0.98) must NOT overwrite an already-verified name (0.99)."""
        state = ThreadState()
        state.personal.name = _field("Gina Author", confidence=0.99)
        state = _apply_facts(state, name="Gina")  # shorter / different
        assert state.personal.name.value == "Gina Author"  # original kept

    def test_overwrites_equal_confidence_name(self):
        """0.98 CRM vs 0.98 existing: incoming wins only when strictly greater — stays."""
        state = ThreadState()
        state.personal.name = _field("G Author", confidence=0.98)
        state = _apply_facts(state, name="Gina Author")
        # 0.98 is NOT strictly greater than 0.98 → original kept
        assert state.personal.name.value == "G Author"

    def test_fills_name_leaves_email_unchanged_when_email_empty(self):
        state = ThreadState()
        state = _apply_facts(state, name="Gina Author")
        assert state.personal.name.value == "Gina Author"
        assert state.personal.email.value is None  # untouched

    def test_no_deltas_when_all_none(self):
        """If no contact data is provided, state must remain unchanged."""
        state = ThreadState()
        state = _apply_facts(state)  # all None
        assert state.personal.name.value is None
        assert state.personal.email.value is None
        assert state.personal.phone.value is None


# ---------------------------------------------------------------------------
# pack_builder: injected facts surface in known_facts and forbidden_reasks
# ---------------------------------------------------------------------------

class TestPackBuilderAfterInjection:
    """After injecting CRM facts, the context pack must include them in known_facts
    and add them to forbidden_reasks so the bot never re-asks."""

    def _state_with_crm_facts(self) -> ThreadState:
        state = ThreadState()
        state = _apply_facts(
            state,
            name="Gina Author",
            email="gina@example.com",
            phone="815-997-0607",
        )
        return state

    def test_name_in_known_facts(self):
        state = self._state_with_crm_facts()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.name" in paths

    def test_email_in_known_facts(self):
        state = self._state_with_crm_facts()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.email" in paths

    def test_phone_in_known_facts(self):
        state = self._state_with_crm_facts()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.phone" in paths

    def test_name_in_forbidden_reasks(self):
        state = self._state_with_crm_facts()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "name" in pack.forbidden_reasks
        assert "your name" in pack.forbidden_reasks

    def test_email_in_forbidden_reasks(self):
        state = self._state_with_crm_facts()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "email" in pack.forbidden_reasks

    def test_phone_in_forbidden_reasks(self):
        state = self._state_with_crm_facts()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "phone" in pack.forbidden_reasks

    def test_correct_values_in_known_facts(self):
        state = self._state_with_crm_facts()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        fact_map = {kf.path: kf.value for kf in pack.known_facts}
        assert fact_map.get("personal.name") == "Gina Author"
        assert fact_map.get("personal.email") == "gina@example.com"
        assert fact_map.get("personal.phone") == "815-997-0607"


# ---------------------------------------------------------------------------
# Bridge URL constant is defined
# ---------------------------------------------------------------------------

def _bridge_source() -> str:
    """Source of the Node bridge that calls /chat/facts, or skip if not checked out.

    The bridge lives in a separate repo, so its location is deployment-specific.
    These tests used to hardcode one developer's laptop path, which meant they
    errored everywhere else and silently guarded nothing.
    """
    candidates = [
        os.environ.get("AI_CHATBOT_BRIDGE_PATH"),
        "/var/www/server.trusoft.pk/src/services/aiChatbotBridge.service.js",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).read_text()
    pytest.skip(
        "Node bridge not present on this host; set AI_CHATBOT_BRIDGE_PATH to run "
        "the bridge contract tests"
    )


class TestBridgeFactsPath:
    """Verify the bridge module defines the facts endpoint path."""

    def test_facts_path_constant_exists(self):
        src = _bridge_source()
        assert "AI_CHATBOT_FACTS_PATH" in src
        assert "/api/v1/chat/facts" in src

    def test_request_inject_facts_exported(self):
        src = _bridge_source()
        assert "export const requestInjectFacts" in src
        assert "requestInjectFacts" in src.split("export default")[1]


class TestBridgeNeverInjectsOnBlurCapture:
    """The on-blur endpoint must not push keystrokes to the bot (chat 5876)."""

    def test_onblur_handler_does_not_sync_facts(self):
        controller = Path("/var/www/server.trusoft.pk/src/controllers/customer.controller.js")
        if not controller.is_file():
            pytest.skip("Node CRM not present on this host")
        src = controller.read_text()

        # updateCustomerSignup is the on-blur handler (every caller sends lead:false).
        start = src.index("const updateCustomerSignup")
        body = src[start : start + 2500]
        # Strip line comments — the handler documents *why* the sync was removed, so a
        # bare substring search would match the explanation rather than a real call.
        code = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("//")
        )
        assert "_syncCustomerFactsToBot(" not in code, (
            "on-blur handler must never push passively-captured contact data to the bot"
        )
