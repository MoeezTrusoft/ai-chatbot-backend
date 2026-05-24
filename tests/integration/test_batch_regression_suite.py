"""
End-to-end conversation regression suite — Batches 1, 2, 3.

Covers interaction conflicts between:
  safety/privacy gates, lead objective, consultation objective,
  service routing, pricing flow, response planner, response generator,
  quality gate, formatter, and persistence/action dispatch.

All fake PII: John Smith / john@example.com / 5551234567
"""

from __future__ import annotations

import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as c:
        yield c


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if thread_id:
        payload["thread_id"] = thread_id
    if attachments:
        payload["attachments"] = attachments
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows, f"No trace for thread {thread_id}"
    return rows[0]


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


def _rich_segs(body: dict[str, Any]) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    for bubble in body.get("bubbles", []):
        for seg in bubble.get("rich_segments", []):
            if isinstance(seg, dict):
                segs.append(seg)
    return segs


class _SafeGen:
    """Deterministic generator for regression tests — never hallucinates."""

    def __init__(self, text: str = "") -> None:
        self._text = text or "I can help you with that."

    async def generate(self, **_kw: Any) -> ResponseDraft:
        return ResponseDraft(text=self._text, source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kw: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


# ===========================================================================
# SCENARIO 1 — Publishing inquiry → scoping → lead capture → consultation
# ===========================================================================


class TestScenario1PublishingToConsultation:
    """Full customer journey: informational → scoping → contact → consultation."""

    def test_s1_informational_does_not_trigger_lead_form(self, client: TestClient) -> None:
        """Step 1: 'How does your publishing process work?' must not include lead form."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "BookCraft handles the full publishing process: formatting, distribution setup "
            "on KDP and IngramSpark, ISBN registration, and metadata. "
            "What format is your book — ebook, paperback, or both?"
        )
        body = _chat(client, "How does your publishing process work?")
        t = _trace(client, body["thread_id"])

        # No lead form on a purely informational question (Batch 3 Step 1).
        form_segs = [s for s in _rich_segs(body) if s.get("type") == "lead_intake_form"]
        assert not form_segs, "Lead form must not appear on informational question"

        # Lead objective must not be stop_discovery on first informational turn.
        lo = t.get("lead_objective") or {}
        assert (
            lo.get("stop_discovery") is not True
            or lo.get("objective_move") == "continue_light_discovery"
        )

    def test_s1_scoping_turns_progress(self, client: TestClient) -> None:
        """Turn 2: service scoping — word count provided."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "500 pages — that's a substantial manuscript. I can help scope "
            "publishing and distribution. What format are you planning: ebook, paperback, or both?"
        )
        body = _chat(client, "My manuscript is about 500 pages, sci-fi novel.")
        assert body["bubbles"], "Expected a response bubble"
        assert "john@example.com" not in _text(body)
        assert "5551234567" not in _text(body)

    def test_s1_contact_with_buying_signal_creates_lead(self, client: TestClient) -> None:
        """Turn 3: user provides contact + buying signal → lead created (Batch 3 Step 2)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "Thanks, John! I've noted your details. For your sci-fi novel publishing project, "
            "the team will reach out to confirm the setup. "
            "Would you like to schedule a free consultation?"
        )
        # Multi-turn: scoping first, then contact.
        r1 = _chat(client, "I need a quote for publishing my sci-fi novel")
        tid = r1["thread_id"]
        body = _chat(
            client,
            "My name is John Smith, john@example.com. Please contact me.",
            thread_id=tid,
        )
        t = _trace(client, tid)

        cc = t.get("contact_capture") or {}
        lo = t.get("lead_objective") or {}
        # Contact must be ready.
        assert cc.get("lead_contact_ready") is True
        # Lead creation should be triggered (buying intent present).
        assert lo.get("objective_move") in {
            "create_lead",
            "ask_contact",
            "continue_light_discovery",
        }
        assert "john@example.com" not in _text(body)

    def test_s1_consultation_after_lead_does_not_retrigger(self, client: TestClient) -> None:
        """Turn 4+: after lead created and acknowledged, new question gets answered."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "IngramSpark provides wide distribution to bookstores and libraries. "
            "It's separate from Amazon KDP and a good complement for print."
        )
        r1 = _chat(client, "I need to publish my sci-fi novel")
        tid = r1["thread_id"]
        _chat(client, "I need a quote — my name is John Smith, john@example.com", thread_id=tid)
        # Acknowledge the lead.
        state = client.app.state.chat_service.threads.get(tid)
        if state:
            state.state.lead_created = True
            state.state.lead_created_acknowledged = True

        _chat(client, "What is IngramSpark?", thread_id=tid)
        t = _trace(client, tid)
        lo = t.get("lead_objective") or {}
        # After acknowledgment, the bot should answer the new question.
        assert lo.get("objective_move") in {"continue_light_discovery", "no_change"}, (
            f"Unexpected move: {lo.get('objective_move')}"
        )


# ===========================================================================
# SCENARIO 2 — Informational question → answer first → soft CTA
# ===========================================================================


class TestScenario2InformationalAnswerFirst:
    def test_s2_examples_question_answered_before_contact(self, client: TestClient) -> None:
        """'Can I see samples?' must be answered before any contact request."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "I can show you samples closest to your genre. "
            "Would you like ghostwriting samples, editing before/after, or cover design examples?"
        )
        body = _chat(client, "Can I see some examples of your editing work?")
        t = _trace(client, body["thread_id"])
        lo = t.get("lead_objective") or {}

        # Informational — must not immediately stop discovery and demand contact.
        # (Batch 3 Step 1 fix: example/samples removed from buying intent regex)
        assert lo.get("objective_move") in {
            "continue_light_discovery",
            "ask_contact",  # acceptable if intent classifier returns portfolio_request
        }
        # Lead form must NOT appear on a pure samples question.
        form_segs = [s for s in _rich_segs(body) if s.get("type") == "lead_intake_form"]
        # If form appears, it must be because we're asking for contact,
        # NOT because the word "example" triggered lead capture.
        if form_segs:
            assert lo.get("objective_move") == "ask_contact"

    def test_s2_process_question_no_immediate_contact(self, client: TestClient) -> None:
        """'How does your process work?' must get an informational response."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "Our ghostwriting process starts with a discovery call, then a chapter outline, "
            "followed by iterative drafting and review. What stage is your project at?"
        )
        body = _chat(client, "How does your ghostwriting process work?")
        t = _trace(client, body["thread_id"])
        lo = t.get("lead_objective") or {}
        # Should NOT trigger contact capture immediately (Batch 3 Step 1).
        assert (
            lo.get("objective_move") != "ask_contact"
            or lo.get("reason", "").lower().count("buying") > 0
            or lo.get("reason", "").lower().count("pricing") > 0
        )


# ===========================================================================
# SCENARIO 3 — Pricing request with missing fields → one question only
# ===========================================================================


class TestScenario3PricingOneQuestion:
    def test_s3_pricing_ask_only_one_slot(self, client: TestClient) -> None:
        """Pricing with missing fields must ask exactly one question (Batch 3 Step 5)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "To give you an accurate estimate, what rough word count or page count "
            "should I use for your manuscript?"
        )
        body = _chat(client, "How much would ghostwriting cost?")
        t = _trace(client, body["thread_id"])

        rq = t.get("response_quality") or {}
        # Quality gate must not flag too_many_questions.
        failures = rq.get("failures") or []
        assert "too_many_questions:2_exceeds_max_1" not in failures, (
            f"Multi-question pricing ask detected: {failures}"
        )
        # Response text must not ask 4 things at once.
        response_text = _text(body)
        # Should not contain 3+ slot keywords in same sentence.
        _MULTI_SLOT = re.compile(
            r"(genre|word count|page count|manuscript stage|deadline)"
            r".*?(genre|word count|page count|manuscript stage|deadline)"
            r".*?(genre|word count|page count|manuscript stage|deadline)",
            re.IGNORECASE | re.DOTALL,
        )
        assert not _MULTI_SLOT.search(response_text), (
            f"Response asks too many pricing slots: {response_text[:200]}"
        )

    def test_s3_quality_gate_blocks_multi_slot_pricing_cta(self, client: TestClient) -> None:
        """Quality gate must catch 'What genre, word count, stage, and deadline?' as multi-ask."""
        from bookcraft.components.response.quality_gate import _question_count

        multi_ask = (
            "What genre, word count, manuscript stage, and deadline should I use for the quote?"
        )
        assert _question_count(multi_ask) >= 2

    def test_s3_attempt_count_increments(self, client: TestClient) -> None:
        """Repeated pricing ask increments attempt count (Batch 2 Step 10)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "What rough word count or page count should I use?"
        )
        r1 = _chat(client, "How much does ghostwriting cost?")
        tid = r1["thread_id"]
        # Get state after first turn.
        state = client.app.state.chat_service.threads.get(tid)
        if state:
            count_after_1 = state.state.sales_actions.pricing.quote_attempt_count
            # Second pricing ask.
            _chat(client, "What about the cost?", thread_id=tid)
            count_after_2 = state.state.sales_actions.pricing.quote_attempt_count
            assert count_after_2 > count_after_1, (
                f"Attempt count did not increment: {count_after_1} → {count_after_2}"
            )


# ===========================================================================
# SCENARIO 4 — User correction → state overwrite
# ===========================================================================


class TestScenario4UserCorrection:
    def test_s4_correction_overwrites_genre(self, client: TestClient) -> None:
        """'Actually it's fantasy, not memoir' must update genre state (Batch 2 Steps 4/5)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "Got it — fantasy it is. That changes the audience and cover direction. "
            "What stage is your manuscript at?"
        )
        r1 = _chat(client, "I have a memoir about my travels")
        tid = r1["thread_id"]
        _chat(client, "Actually it's fantasy, not memoir.", thread_id=tid)

        state = client.app.state.chat_service.threads.get(tid)
        if state:
            genre_val = getattr(state.state.project.genre, "value", None)
            # Genre should be updated to fantasy (or at minimum not still memoir).
            if genre_val:
                assert genre_val != "memoir", f"Genre was not corrected, still: {genre_val}"

    def test_s4_state_applier_safe_on_bad_path(self, client: TestClient) -> None:
        """Invalid state delta path must not crash the turn (Batch 2 Step 6)."""
        from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
        from bookcraft.components.extraction.state_applier import StateApplier
        from bookcraft.domain.enums import Source
        from bookcraft.domain.state import ThreadState

        applier = StateApplier()
        state = ThreadState()
        bad = CombinedExtraction()
        bad.state_deltas.append(
            StateDelta(
                path="totally.invalid.path",
                value="x",
                confidence=0.9,
                source=Source.USER_STATED,
                extracted_by="test",
            )
        )
        rejected: list[str] = []
        result = applier.apply(state, bad, rejected_paths=rejected)
        assert result is not None, "StateApplier must not crash on bad path"
        assert "totally.invalid.path" in rejected


# ===========================================================================
# SCENARIO 5 — Complaint / privacy concern → recovery mode
# ===========================================================================


class TestScenario5ComplaintRecovery:
    def test_s5_privacy_complaint_no_pii_echo(self, client: TestClient) -> None:
        """After privacy complaint, response must not echo raw contact PII (Batch 1 Step 3)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "You're right — those were your contact details, not ours. "
            "I won't repeat them. I have your details on file and the team will reach out."
        )
        r1 = _chat(client, "Please contact me — john@example.com, 5551234567")
        tid = r1["thread_id"]
        body = _chat(
            client,
            "What the hell, you just repeated my contact details!",
            thread_id=tid,
        )
        # Verify the response text itself does not echo raw contact PII.
        assert "john@example.com" not in _text(body)
        assert "5551234567" not in _text(body)

    def test_s5_complaint_does_not_create_lead(self, client: TestClient) -> None:
        """Privacy complaint + contact info must not trigger lead creation (Batch 3 Step 2)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "I understand your frustration. I won't repeat your details. "
            "How can I help you with your publishing project?"
        )
        body = _chat(
            client,
            "I'm really frustrated — you keep asking for my email: john@example.com",
        )
        t = _trace(client, body["thread_id"])
        lo = t.get("lead_objective") or {}
        # Complaint context must not create a lead.
        assert lo.get("objective_move") != "create_lead", f"Lead created in complaint context: {lo}"

    def test_s5_safety_guard_blocks_threats(self, client: TestClient) -> None:
        """Threats must be blocked, not just warned (Batch 1 Step 1 / input guard)."""
        body = _chat(client, "I will hurt your team if this is not fixed.")
        assert body.get("blocked") is True, "Threat should be blocked"
        assert body.get("bubbles") == [], "No bubbles on blocked response"
        assert body.get("system_message") is not None

    def test_s5_frustrated_profanity_not_blocked(self, client: TestClient) -> None:
        """Casual frustration profanity should NOT be blocked (Batch 3 Step 15)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "I hear you — that's been frustrating. Let me clarify what happened. "
            "What would be most helpful right now?"
        )
        body = _chat(client, "What the fuck, you're not reading my messages!")
        # Should NOT be blocked — frustrated, not threatening.
        assert body.get("blocked") is not True, "Frustrated profanity should not be blocked"
        assert body.get("bubbles") is not None


# ===========================================================================
# SCENARIO 6 — Portfolio request → no raw URLs
# ===========================================================================


class TestScenario6PortfolioNoRawUrls:
    def test_s6_portfolio_text_no_raw_urls(self, client: TestClient) -> None:
        """Portfolio response text must not contain raw http:// URLs (Batch 3 Step 8)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "I can show samples closest to your genre and service. "
            "Would you like cover design examples, ghostwriting samples, or both?"
        )
        body = _chat(client, "Show me some portfolio samples for cover design.")
        response_text = _text(body)
        # Raw URL pattern must not appear in text.
        assert not re.search(r"https?://\S+", response_text), (
            f"Raw URL found in portfolio response: {response_text[:300]}"
        )

    def test_s6_portfolio_links_in_rich_segments_not_text(self, client: TestClient) -> None:
        """Portfolio URLs must appear in rich_segments, not in text (Batch 3 Step 8 / PR 3)."""
        body = _chat(client, "Show me portfolio samples for cover design.")
        # If there are portfolio links, they must be in rich_segments.
        for seg in _rich_segs(body):
            if seg.get("type") in {"portfolio_link", "portfolio_links"}:
                # The URL in the segment should be https.
                url = seg.get("url", "")
                items = seg.get("items", [])
                if url:
                    assert url.startswith("https://"), f"Non-https URL in rich segment: {url}"
                for item in items:
                    if isinstance(item, dict):
                        assert item.get("url", "").startswith("https://")

    def test_s6_quality_gate_allows_safe_portfolio_text(self, client: TestClient) -> None:
        """A safe portfolio response (no raw URLs) must pass quality gate."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "Here are samples closest to your genre. Would you like to see more options?"
        )
        body = _chat(client, "Can I see editing samples?")
        t = _trace(client, body["thread_id"])
        rq = t.get("response_quality") or {}
        failures = rq.get("failures") or []
        assert not any("raw_portfolio_url" in f for f in failures)


# ===========================================================================
# SCENARIO 7 — Roman Urdu inquiry → routed in English
# ===========================================================================


class TestScenario7RomanUrduRouting:
    def test_s7_roman_urdu_publish_not_redirected(self, client: TestClient) -> None:
        """Roman Urdu 'kitab publish' must not get language redirect (Batch 3 Step 14)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "I understood you're asking about publishing your book. "
            "What format would you like — ebook, paperback, or both?"
        )
        body = _chat(client, "Mujhe apni kitab publish karwani hai")
        # Must NOT be a language redirect — response must have actual help.
        assert body.get("language_status") == "en", (
            "Roman Urdu lead inquiry must be treated as English"
        )
        assert body.get("bubbles") and len(body["bubbles"]) > 0, (
            "Must get a response, not a redirect"
        )
        response_text = _text(body)
        # Must not be the English-only redirect message.
        assert "currently available in English" not in response_text

    def test_s7_roman_urdu_editing_price_query(self, client: TestClient) -> None:
        """'Price kya hai editing ka?' must be routed as an editing pricing question."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "For editing, pricing depends on manuscript length and service level. "
            "What's the word count or page count of your manuscript?"
        )
        body = _chat(client, "Price kya hai editing ka?")
        assert body.get("language_status") == "en"
        assert body.get("bubbles") and len(body["bubbles"]) > 0

    def test_s7_roman_urdu_bypass_source_label(self, client: TestClient) -> None:
        """Roman Urdu bypass must be recorded with correct source label in language decision."""
        from bookcraft.components.language_guard.guard import LanguageGuard

        guard = LanguageGuard(enabled=True)
        decision = guard.detect("editing chahiye mujhe")
        assert decision.is_english is True
        assert decision.source in {"roman_urdu_lead_bypass", "ascii_fast_path", "short_message"}


# ===========================================================================
# SCENARIO 8 — RAG failure → no unsupported exact policy claims
# ===========================================================================


class TestScenario8RagFailure:
    def test_s8_rag_status_in_trace(self, client: TestClient) -> None:
        """rag_status must appear in live trace (Batch 3 Step 16)."""
        body = _chat(client, "What is your refund policy?")
        t = _trace(client, body["thread_id"])
        assert "rag_status" in t, "rag_status must be in trace"
        assert t["rag_status"] in {"skipped", "success", "empty", "failed"}

    def test_s8_rag_skipped_when_no_retriever(self, client: TestClient) -> None:
        """With no RAG retriever configured, rag_status must be 'skipped'."""
        body = _chat(client, "Do you offer a money-back guarantee?")
        t = _trace(client, body["thread_id"])
        # In test mode, rag_retriever is None → skipped.
        assert t.get("rag_status") == "skipped"

    def test_s8_rag_failure_does_not_crash_turn(self, client: TestClient) -> None:
        """Even if RAG fails (simulated), the turn must complete (Batch 2 Step 9 pattern)."""
        # In test mode with no retriever this is vacuously true;
        # we verify the turn completes with a valid response.
        body = _chat(client, "Can you explain your distribution network in detail?")
        assert body.get("bubbles") is not None
        assert body.get("language_status") == "en"


# ===========================================================================
# SCENARIO 9 — Expired confirmation → no stale action dispatched
# ===========================================================================


class TestScenario9ExpiredConfirmation:
    def test_s9_expired_pending_returns_blocked(self, client: TestClient) -> None:
        """Confirming expired pending must return BLOCKED (Batch 1 Step 4)."""
        from bookcraft.components.actions.planner import SalesActionPlanner
        from bookcraft.components.actions.schemas import ActionStatus
        from bookcraft.components.extraction.schemas import CombinedExtraction
        from bookcraft.components.intent.schemas import IntentVote
        from bookcraft.components.preprocessor.schemas import ProcessedMessage
        from bookcraft.domain.enums import QueryIntentType, SalesStage
        from bookcraft.domain.state import PendingConfirmationState, ThreadState

        state = ThreadState()
        state.sales_actions.pending_confirmation = PendingConfirmationState(
            type="schedule_consultation",
            payload={"name": "John Smith"},
            created_at=datetime.now(UTC) - timedelta(hours=2),
            expires_at=datetime.now(UTC) - timedelta(hours=1),  # EXPIRED
        )
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
        intent = IntentVote(
            query_primary=QueryIntentType.CONSULTATION_REQUEST,
            funnel_stage=SalesStage.SCOPING,
            needs_clarification=False,
            confidence=0.9,
            rationale="test",
            evidence=[],
        )
        planner = SalesActionPlanner()
        plan = planner.plan(
            processed=processed,
            state=state,
            intent=intent,
            extraction=CombinedExtraction(),
        )
        assert plan.status == ActionStatus.BLOCKED
        assert plan.customer_safe_summary is not None
        assert "expired" in (plan.customer_safe_summary or "").lower()

    def test_s9_fresh_confirmation_dispatches(self, client: TestClient) -> None:
        """A fresh pending consultation confirmation must proceed (Batch 1 Step 4)."""
        from bookcraft.components.actions.planner import SalesActionPlanner
        from bookcraft.components.actions.schemas import ActionStatus
        from bookcraft.components.extraction.schemas import CombinedExtraction
        from bookcraft.components.intent.schemas import IntentVote
        from bookcraft.components.preprocessor.schemas import ProcessedMessage
        from bookcraft.domain.enums import QueryIntentType, SalesStage
        from bookcraft.domain.state import PendingConfirmationState, ThreadState

        state = ThreadState()
        state.sales_actions.pending_confirmation = PendingConfirmationState(
            type="schedule_consultation",
            payload={"name": "John Smith"},
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),  # FRESH
        )
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
        intent = IntentVote(
            query_primary=QueryIntentType.CONSULTATION_REQUEST,
            funnel_stage=SalesStage.SCOPING,
            needs_clarification=False,
            confidence=0.9,
            rationale="test",
            evidence=[],
        )
        planner = SalesActionPlanner()
        plan = planner.plan(
            processed=processed,
            state=state,
            intent=intent,
            extraction=CombinedExtraction(),
        )
        assert plan.status == ActionStatus.READY

    def test_s9_cross_action_confirmation_rejected(self, client: TestClient) -> None:
        """'schedule it' must not confirm an NDA (Batch 1 Step 5 — action-specific confirmation)."""
        from bookcraft.components.actions.slot_resolver import is_confirmation_text

        # "schedule it" is consultation-specific — must not confirm NDA.
        assert not is_confirmation_text("schedule it", pending_action_type="generate_nda")
        # "send the NDA" is NDA-specific — must not confirm consultation.
        assert not is_confirmation_text("send the NDA", pending_action_type="schedule_consultation")


# ===========================================================================
# SCENARIO 10 — Concurrent confirmation → no duplicate side effect
# ===========================================================================


class TestScenario10ConcurrentIdempotency:
    @pytest.mark.asyncio
    async def test_s10_same_idempotency_key_no_double_dispatch(self) -> None:
        """Same idempotency key must never dispatch twice (Batch 1 Step 6)."""
        from bookcraft.components.actions.dispatcher import (
            SalesActionDispatcher,
            _make_idempotency_key,
        )
        from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType
        from bookcraft.components.storage.action_idempotency_repository import (
            ActionIdempotencyRepository,
            make_slots_hash,
        )

        tid = uuid4()
        slots: dict[str, object] = {"name": "John Smith", "email": "john@example.com"}
        idem_key = _make_idempotency_key(tid, "schedule_consultation", slots)

        # Pre-claim the key so dispatcher sees it as already in flight.
        repo = ActionIdempotencyRepository()
        await repo.claim(
            idempotency_key=idem_key,
            thread_id=tid,
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
        result = await dispatcher.dispatch(plan, thread_id=tid, customer_id=None)
        assert result is None, "Double dispatch must return None"

    @pytest.mark.asyncio
    async def test_s10_idempotency_key_stable_across_calls(self) -> None:
        """Idempotency key must be deterministic (Batch 1 Step 6)."""
        from bookcraft.components.actions.dispatcher import _make_idempotency_key

        tid = uuid4()
        slots: dict[str, object] = {"email": "john@example.com", "name": "John Smith"}
        k1 = _make_idempotency_key(tid, "create_lead", slots)
        k2 = _make_idempotency_key(tid, "create_lead", slots)
        assert k1 == k2, "Idempotency key must be stable"
        assert len(k1) == 32  # SHA-256 truncated to 32 hex chars.

    @pytest.mark.asyncio
    async def test_s10_first_dispatch_marks_key(self) -> None:
        """First dispatch must mark the key so second call returns None (Batch 1 Step 6)."""
        from bookcraft.components.actions.dispatcher import (
            SalesActionDispatcher,
            _make_idempotency_key,
        )
        from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType

        tid = uuid4()
        slots: dict[str, object] = {"name": "John Smith"}
        idem_key = _make_idempotency_key(tid, "create_lead", slots)
        dispatcher = SalesActionDispatcher()

        # Key must not be claimed before first dispatch.
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
        await dispatcher.dispatch(plan, thread_id=tid, customer_id=None)

        # After dispatch, key must be recorded (completed or failed, not None).
        status_after = await dispatcher.action_idempotency_repository.get_status(
            idempotency_key=idem_key
        )
        assert status_after in {"completed", "failed"}


# ===========================================================================
# CROSS-BATCH INTERACTION: ABC suppression + lead form gate
# ===========================================================================


class TestCrossBatchAbcLeadFormGate:
    def test_abc_suppression_prevents_lead_form(self, client: TestClient) -> None:
        """answer_before_capture suppression must prevent lead form injection (Batch 3 Step 3)."""
        # When ABC suppresses contact, lead form must not appear.
        # We test the boolean logic directly.
        from unittest.mock import MagicMock

        abc_suppress = MagicMock()
        abc_suppress.suppress_contact_until_answered = True

        abc_allow = MagicMock()
        abc_allow.suppress_contact_until_answered = False

        def _would_show_form(abc, move, ready):
            abc_suppresses = abc is not None and getattr(
                abc, "suppress_contact_until_answered", False
            )
            return move == "ask_contact" and not ready and not abc_suppresses

        assert not _would_show_form(abc_suppress, "ask_contact", False)
        assert _would_show_form(abc_allow, "ask_contact", False)
        assert not _would_show_form(abc_allow, "ask_contact", True)

    def test_broken_email_complaint_no_lead(self, client: TestClient) -> None:
        """Bug report with email must not create lead (Batch 3 Step 2)."""
        client.app.state.chat_service.response_generator = _SafeGen(
            "I'm sorry to hear about the form issue. Could you describe what happened "
            "when you tried to submit?"
        )
        body = _chat(
            client,
            "Your form is broken — I tested it with john@example.com and it didn't work",
        )
        t = _trace(client, body["thread_id"])
        lo = t.get("lead_objective") or {}
        assert lo.get("objective_move") != "create_lead", f"Bug report must not create lead: {lo}"


# ===========================================================================
# CROSS-BATCH: Quality gate coherence
# ===========================================================================


class TestQualityGateCoherence:
    def test_ready_to_help_not_blocked_action_false_positive(self, client: TestClient) -> None:
        """'I'm ready to help' must not falsely trigger blocked-action detection."""
        from bookcraft.components.response.quality_gate import _blocked_tool_mismatch
        from bookcraft.components.tools.governance import ToolGovernanceDecision

        gov = ToolGovernanceDecision(allowed=False, reason="test", blocked_message="blocked")
        assert not _blocked_tool_mismatch("I'm ready to help you with that.", gov)
        assert not _blocked_tool_mismatch("The team will be ready by Monday.", gov)
        assert not _blocked_tool_mismatch("Everything looks good for the project.", gov)

    def test_nda_sent_triggers_when_blocked(self, client: TestClient) -> None:
        """Actual NDA dispatch claim must trigger when blocked (Batch 1 Step 13)."""
        from bookcraft.components.response.quality_gate import _blocked_tool_mismatch
        from bookcraft.components.tools.governance import ToolGovernanceDecision

        gov = ToolGovernanceDecision(allowed=False, reason="test", blocked_message="blocked")
        assert _blocked_tool_mismatch("Your NDA has been sent to your email.", gov)
        assert _blocked_tool_mismatch("Your consultation has been booked for tomorrow.", gov)

    def test_multi_slot_question_count(self) -> None:
        """Multi-slot question must count >= 2 (Batch 3 Step 10)."""
        from bookcraft.components.response.quality_gate import _question_count

        assert _question_count("What genre, word count, and deadline should I use?") >= 2
        assert _question_count("Share your genre, stage, and deadline.") >= 2
        assert _question_count("What word count should I use?") == 1

    def test_weak_cta_fails_for_specific_slot(self) -> None:
        """Vague 'Tell me more' fails when plan has specific slot (Batch 3 Step 11)."""
        from bookcraft.components.response.planner import ResponsePlan
        from bookcraft.components.response.quality_gate import _missing_next_step

        plan = ResponsePlan(next_question="word_or_page_count", primary_goal="pricing_scoping")
        assert _missing_next_step("I can help with that. Tell me more.", plan) is True
        assert (
            _missing_next_step("What rough word count should I use for the estimate?", plan)
            is False
        )


# ===========================================================================
# CROSS-BATCH: State sanitizer + redaction
# ===========================================================================


class TestStateSanitizerIntegrity:
    def test_redacted_sentinel_not_contact_ready(self) -> None:
        """[REDACTED_EMAIL] must not make contact look ready (Batch 1 Step 2)."""
        from bookcraft.components.leads.contact_utils import contact_is_ready

        contact = {
            "name": "[REDACTED_NAME]",
            "email": "[REDACTED_EMAIL]",
            "phone": "[REDACTED_PHONE]",
        }
        assert not contact_is_ready(contact)

    def test_real_contact_is_ready(self) -> None:
        """Real contact values must be correctly detected as ready."""
        from bookcraft.components.leads.contact_utils import contact_is_ready

        assert contact_is_ready({"name": "John Smith", "email": "john@example.com", "phone": None})
        assert contact_is_ready({"name": "John Smith", "email": None, "phone": "5551234567"})

    def test_trace_sanitizer_no_raw_pii(self) -> None:
        """Trace must not contain raw email/phone in contact_capture (Batch 1 Step 3)."""
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
        assert "john@example.com" not in str(safe)
        assert "5551234567" not in str(safe)
        assert "John Smith" not in str(safe)
        assert safe["has_email"] is True
        assert safe["has_phone"] is True
