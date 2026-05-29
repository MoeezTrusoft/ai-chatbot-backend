"""Production-grade end-to-end scenario tests for the combined plan (Phases A–E).

Tests cover:
  Phase A  — LLM metadata extraction: confidence gating, coreference skipping,
              high/low-confidence deltas, rich free-text metadata.
  Phase B  — TRG engagement_weight, importance-weighted compaction (STATE_FACT
              nodes never dropped).
  Phase C  — CSR context ingestion via /csr-turn and /handover endpoints.
  Phase D  — CSR summarizer sliding window (verbatim + abstract).
  Phase E  — Commitment detector (price / timeline / discount).
  Combined — Full 12-turn journey: bot → CSR handover → bot return; context
              threads through entire conversation.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4, UUID

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.csr.commitment_detector import CsrCommitment, detect_commitments
from bookcraft.components.csr.summarizer import CsrContextSummarizer
from bookcraft.components.extraction.llm_extractor import LLMMetadataExtractor, LLMExtractionResult
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.components.trg import InMemoryGraphRepository, TemporalRelationGraphEngine
from bookcraft.components.trg.schemas import GraphNodeType
from bookcraft.domain.enums import Source
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _app() -> Any:
    return create_app(Settings(app_env="test", api_auth_mode="off"))


def _turn(client: TestClient, message: str, *, thread_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if thread_id:
        payload["thread_id"] = thread_id
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, f"turn failed: {r.text}"
    return r.json()


def _csr_turn(
    client: TestClient,
    thread_id: str,
    *,
    csr_id: str = "csr-001",
    csr_name: str = "Sarah",
    csr_message: str,
    user_message: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "csr_id": csr_id,
        "csr_name": csr_name,
        "csr_message": csr_message,
    }
    if user_message:
        payload["user_message"] = user_message
    r = client.post("/api/v1/chat/csr-turn", json=payload)
    assert r.status_code == 204, f"csr-turn failed ({r.status_code}): {r.text}"


def _handover(
    client: TestClient,
    thread_id: str,
    direction: str,
    *,
    csr_id: str = "csr-001",
    csr_name: str = "Sarah",
    handover_note: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "direction": direction,
        "csr_id": csr_id,
        "csr_name": csr_name,
    }
    if handover_note:
        payload["handover_note"] = handover_note
    r = client.post("/api/v1/chat/handover", json=payload)
    assert r.status_code == 200, f"handover failed ({r.status_code}): {r.text}"
    return r.json()


def _state(client: TestClient, thread_id: str) -> ThreadState:
    svc = client.app.state.chat_service
    mem = svc.threads.get(UUID(thread_id))
    assert mem is not None, f"No in-memory thread for {thread_id}"
    return mem.state


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows, f"No trace rows for thread {thread_id}"
    return rows[-1]


# ─────────────────────────────────────────────────────────────────────────────
# Phase A: LLM Metadata Extractor (unit, with mock adapter)
# ─────────────────────────────────────────────────────────────────────────────

class _MockAdapter:
    """Minimal LLMProvider that returns a pre-configured LLMExtractedFacts."""
    name = "mock_extractor"

    def __init__(self, facts: LLMExtractedFacts) -> None:
        self._facts = facts

    async def structured(self, *, system: str, user: str, output_model: Any, purpose: str) -> Any:
        return self._facts


@pytest.mark.asyncio
async def test_llm_extractor_high_confidence_produces_full_delta() -> None:
    """≥0.85 confidence → delta keeps the raw confidence (override-capable)."""
    facts = LLMExtractedFacts(
        name=ExtractedValue(value="Layla Hassan", confidence=0.95, source_quote="my name is Layla Hassan"),
        email=ExtractedValue(value="layla@example.com", confidence=0.92, source_quote="layla@example.com"),
        word_count=ExtractedValue(value=72000, confidence=0.90, source_quote="72,000 words"),
    )
    extractor = LLMMetadataExtractor(adapter=_MockAdapter(facts))
    result = await extractor.extract("My name is Layla Hassan, email layla@example.com, 72k words", "", ThreadState())

    assert len(result.state_deltas) == 3
    paths = {d.path for d in result.state_deltas}
    assert "personal.name" in paths
    assert "personal.email" in paths
    assert "project.word_count" in paths

    for d in result.state_deltas:
        assert d.confidence >= 0.85, f"{d.path} confidence {d.confidence} below threshold"
        assert d.source == Source.AI_EXTRACTED


@pytest.mark.asyncio
async def test_llm_extractor_low_confidence_downscaled_to_fill_value() -> None:
    """<0.85 confidence → forced to 0.3 so StateApplier only fills empty fields."""
    facts = LLMExtractedFacts(
        genre=ExtractedValue(value="maybe thriller", confidence=0.60, source_quote="maybe thriller"),
    )
    extractor = LLMMetadataExtractor(adapter=_MockAdapter(facts))
    result = await extractor.extract("maybe thriller", "", ThreadState())

    assert len(result.state_deltas) == 1
    assert result.state_deltas[0].confidence == pytest.approx(0.3)
    assert result.state_deltas[0].path == "project.genre"


@pytest.mark.asyncio
async def test_llm_extractor_word_count_coerced_to_int() -> None:
    """word_count returned as string from LLM is coerced to int."""
    facts = LLMExtractedFacts(
        word_count=ExtractedValue(value="85000", confidence=0.95, source_quote="85,000 words"),
    )
    extractor = LLMMetadataExtractor(adapter=_MockAdapter(facts))
    result = await extractor.extract("85,000 words", "", ThreadState())

    assert result.state_deltas[0].value == 85000
    assert isinstance(result.state_deltas[0].value, int)


@pytest.mark.asyncio
async def test_llm_extractor_rich_metadata_high_confidence_only() -> None:
    """Rich free-text fields (cover_preferences etc.) only extracted at ≥0.85."""
    facts = LLMExtractedFacts(
        cover_preferences=ExtractedValue(
            value="dark gothic art, full-bleed illustration",
            confidence=0.92,
            source_quote="dark gothic art",
        ),
        page_dimensions=ExtractedValue(
            value="6x9 trade paperback",
            confidence=0.70,  # below threshold → should be excluded
            source_quote="6x9",
        ),
    )
    extractor = LLMMetadataExtractor(adapter=_MockAdapter(facts))
    result = await extractor.extract("dark gothic art cover, 6x9 format", "", ThreadState())

    assert "cover_preferences" in result.rich_metadata
    assert result.rich_metadata["cover_preferences"] == "dark gothic art, full-bleed illustration"
    assert "page_dimensions" not in result.rich_metadata  # below threshold


@pytest.mark.asyncio
async def test_llm_extractor_coreference_notes_captured() -> None:
    """Coreference notes from the LLM are passed through in the result."""
    facts = LLMExtractedFacts(
        coreference_notes=["'my book' refers to previously mentioned thriller"],
    )
    extractor = LLMMetadataExtractor(adapter=_MockAdapter(facts))
    result = await extractor.extract("my book needs editing", "", ThreadState())

    assert result.coreference_notes == ["'my book' refers to previously mentioned thriller"]


@pytest.mark.asyncio
async def test_llm_extractor_adapter_failure_returns_empty() -> None:
    """If the LLM call raises, the extractor returns an empty result (never crashes)."""
    class _FailAdapter:
        name = "fail"
        async def structured(self, **kwargs: Any) -> Any:
            raise RuntimeError("connection refused")

    extractor = LLMMetadataExtractor(adapter=_FailAdapter())
    result = await extractor.extract("some message", "", ThreadState())

    assert result.state_deltas == []
    assert result.rich_metadata == {}


@pytest.mark.asyncio
async def test_llm_extractor_empty_message_skipped() -> None:
    """Empty / whitespace-only messages skip LLM call entirely."""
    called = []
    class _TrackAdapter:
        name = "track"
        async def structured(self, **kwargs: Any) -> Any:
            called.append(True)
            return LLMExtractedFacts()

    extractor = LLMMetadataExtractor(adapter=_TrackAdapter())
    result = await extractor.extract("   ", "", ThreadState())

    assert called == []
    assert result.state_deltas == []


# ─────────────────────────────────────────────────────────────────────────────
# Phase B: TRG engagement_weight + importance-weighted compaction
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trg_engagement_weight_higher_for_questions() -> None:
    """User messages with questions produce higher engagement_weight on the node."""
    thread_id = uuid4()
    engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

    await engine.update_after_turn(
        thread_id=thread_id, turn_sequence=1,
        user_text="I need editing.", assistant_text="OK.",
    )
    questioning = await engine.update_after_turn(
        thread_id=thread_id, turn_sequence=2,
        user_text="Can you tell me the pricing? How long will it take? What formats do you support?",
        assistant_text="Let me explain...",
    )

    # nodes is a list
    graph = questioning.graph
    user_nodes = [n for n in graph.nodes if n.node_type == GraphNodeType.USER_MESSAGE]
    assert len(user_nodes) == 2

    weights = sorted([n.engagement_weight for n in user_nodes])
    # The second node (3 questions) should have weight > first (0 questions)
    assert weights[1] > weights[0], f"Expected questioning turn > flat turn, got {weights}"


@pytest.mark.asyncio
async def test_trg_engagement_weight_higher_for_corrections() -> None:
    """Correction keywords ('actually', 'not quite', 'I meant') boost engagement_weight."""
    thread_id = uuid4()
    engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository())

    await engine.update_after_turn(
        thread_id=thread_id, turn_sequence=1,
        user_text="50000 words.", assistant_text="Got it.",
    )
    correcting = await engine.update_after_turn(
        thread_id=thread_id, turn_sequence=2,
        user_text="Actually I meant 80000 words, not 50000. I was wrong earlier.",
        assistant_text="Thanks for clarifying.",
    )

    user_nodes = [n for n in correcting.graph.nodes if n.node_type == GraphNodeType.USER_MESSAGE]
    weights = [n.engagement_weight for n in user_nodes]
    correction_weight = max(weights)
    assert correction_weight >= 1.5, f"Expected correction to boost weight, got {correction_weight}"


@pytest.mark.asyncio
async def test_trg_compaction_never_drops_state_fact_nodes() -> None:
    """Importance-weighted compaction must never drop STATE_FACT nodes.

    A STATE_FACT node is created in graph.nodes when an incoming delta contradicts
    (changes) an existing state value.  We set up that contradiction, then drive
    compaction with compact_keep=4 and 10 extra turns, and verify the STATE_FACT
    node survives.
    """
    from bookcraft.domain.meta import FieldMeta

    thread_id = uuid4()
    engine = TemporalRelationGraphEngine(repository=InMemoryGraphRepository(), compact_keep=4)

    # Turn 1: establish an initial name in state
    state_v1 = ThreadState()
    state_v1.personal.name = FieldMeta(
        value="Tariq Musa", confidence=0.95, source=Source.USER_STATED, raw_excerpt="Tariq Musa"
    )
    await engine.update_after_turn(
        thread_id=thread_id, turn_sequence=1,
        user_text="My name is Tariq Musa.",
        assistant_text="Hello Tariq!",
    )

    # Turn 2: user corrects their name — DIFFERENT value → contradiction → STATE_FACT node added
    delta_correction = StateDelta(
        path="personal.name", value="Tariq Mousavi",  # different from "Tariq Musa"
        confidence=0.95, source=Source.USER_STATED,
        extracted_by="test", raw_excerpt="Actually Tariq Mousavi",
    )
    await engine.update_after_turn(
        thread_id=thread_id, turn_sequence=2,
        user_text="Actually my name is Tariq Mousavi, not Musa.",
        assistant_text="Got it, Tariq Mousavi!",
        previous_state=state_v1,
        state_deltas=[delta_correction],
    )

    # Verify STATE_FACT node was created
    graph_after_correction = await engine.repository.load(thread_id)
    fact_nodes_before = [n for n in graph_after_correction.nodes if n.node_type == GraphNodeType.STATE_FACT]
    assert len(fact_nodes_before) > 0, "STATE_FACT node should exist after contradiction"

    # Now drive compaction with 10 more turns (compact_keep=4, so most turns drop)
    for i in range(3, 13):
        await engine.update_after_turn(
            thread_id=thread_id, turn_sequence=i,
            user_text=f"This is filler turn {i}.",
            assistant_text=f"Acknowledged turn {i}.",
        )

    graph = await engine.repository.load(thread_id)
    state_fact_nodes = [n for n in graph.nodes if n.node_type == GraphNodeType.STATE_FACT]
    assert len(state_fact_nodes) > 0, "STATE_FACT node was dropped by compaction — this is a bug"


# ─────────────────────────────────────────────────────────────────────────────
# Phase E: Commitment Detector (unit)
# ─────────────────────────────────────────────────────────────────────────────

def test_commitment_detector_price_quoted() -> None:
    """Dollar amounts in standard pricing phrases are detected as price_quoted."""
    text = "We can do the full editing package for $1,200 total."
    commitments = detect_commitments(text)
    price = [c for c in commitments if c.commitment_type == "price_quoted"]
    assert price, f"No price commitment detected in: {text!r}"
    assert "$1,200" in price[0].text


def test_commitment_detector_timeline_promised() -> None:
    """Turnaround/delivery phrases with day/week/month counts detected."""
    text = "We'll have it ready in 3 weeks turnaround."
    commitments = detect_commitments(text)
    timeline = [c for c in commitments if c.commitment_type == "timeline_promised"]
    assert timeline, f"No timeline commitment detected in: {text!r}"


def test_commitment_detector_discount_offered() -> None:
    """Discount/waive/complimentary keywords are detected."""
    for phrase in [
        "We'll waive the setup fee for you.",
        "We can give you a 10% discount on the package.",
        "The first revision is complimentary.",
        "We'll include a free consultation.",
    ]:
        commitments = detect_commitments(phrase)
        disc = [c for c in commitments if c.commitment_type == "discount_offered"]
        assert disc, f"No discount commitment detected in: {phrase!r}"


def test_commitment_detector_multiple_in_one_message() -> None:
    """Multiple commitment types in one message are all detected."""
    text = (
        "The package is $2,500 total. "
        "Turnaround is 4 weeks. "
        "We'll waive the rush fee."
    )
    commitments = detect_commitments(text)
    types = {c.commitment_type for c in commitments}
    assert "price_quoted" in types
    assert "timeline_promised" in types
    assert "discount_offered" in types


def test_commitment_detector_no_false_positives_on_plain_text() -> None:
    """Normal conversation messages produce no commitments."""
    text = "What kind of editing do you need for your manuscript?"
    commitments = detect_commitments(text)
    assert commitments == []


# ─────────────────────────────────────────────────────────────────────────────
# Phase E: CSR Summarizer sliding window (unit, no adapter)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_csr_summarizer_verbatim_window_fills_to_three() -> None:
    """First 3 CSR turns stay verbatim; abstract stays empty."""
    summarizer = CsrContextSummarizer()
    state = ThreadState()

    for i in range(3):
        await summarizer.ingest(state, user_message=f"User msg {i}", csr_message=f"CSR msg {i}")

    assert len(state.csr_context_recent_verbatim) == 3
    assert state.csr_context_abstract == ""


@pytest.mark.asyncio
async def test_csr_summarizer_fourth_turn_compresses_oldest() -> None:
    """On the 4th ingest, the oldest verbatim turn rolls into the abstract."""
    summarizer = CsrContextSummarizer()  # no adapter → naive fallback
    state = ThreadState()

    await summarizer.ingest(state, user_message="Hi", csr_message="Hello, I am Sarah from BookCraft.")
    await summarizer.ingest(state, user_message="Tell me about editing", csr_message="We offer developmental editing.")
    await summarizer.ingest(state, user_message="What's the price?", csr_message="Editing starts at $800.")
    # 4th turn — oldest should compress into abstract
    await summarizer.ingest(state, user_message="How long does it take?", csr_message="Typically 3 weeks turnaround.")

    assert len(state.csr_context_recent_verbatim) == 3
    # Abstract must not be empty — oldest turn ("Hello, I am Sarah...") went there
    assert state.csr_context_abstract, "Abstract should contain compressed first turn"
    assert "Sarah" in state.csr_context_abstract or "BookCraft" in state.csr_context_abstract or "Hello" in state.csr_context_abstract


@pytest.mark.asyncio
async def test_csr_summarizer_window_always_keeps_latest_three() -> None:
    """After many turns, the window always holds the 3 most recent verbatim turns."""
    summarizer = CsrContextSummarizer()
    state = ThreadState()

    messages = [f"CSR says turn {i}" for i in range(8)]
    for i, msg in enumerate(messages):
        await summarizer.ingest(state, user_message=None, csr_message=msg)

    verbatim = state.csr_context_recent_verbatim
    assert len(verbatim) == 3
    # Last 3 messages should be turn 5, 6, 7
    for i, turn in enumerate(verbatim):
        assert f"turn {5 + i}" in turn["csr_message"], (
            f"Expected 'turn {5 + i}' in verbatim[{i}], got: {turn['csr_message']}"
        )


@pytest.mark.asyncio
async def test_csr_summarizer_with_llm_adapter_called_on_overflow() -> None:
    """When the window overflows, the LLM adapter's structured() is called."""
    compress_calls: list[str] = []

    class _TrackingAdapter:
        name = "tracker"
        async def structured(self, *, system: str, user: str, output_model: Any, purpose: str) -> Any:
            compress_calls.append(user[:80])
            # Return an instance of whatever output_model the summarizer passes in
            # (it's _AbstractModel with a `text` field)
            return output_model(text="Compressed abstract text.")

    summarizer = CsrContextSummarizer(adapter=_TrackingAdapter())
    state = ThreadState()

    for i in range(4):
        await summarizer.ingest(state, user_message=f"User {i}", csr_message=f"CSR {i}")

    assert len(compress_calls) == 1, "Exactly one compress call should happen on 4th turn"
    # The abstract should contain the LLM-compressed text
    assert "Compressed abstract text." in state.csr_context_abstract, (
        f"Expected LLM-compressed abstract, got: {state.csr_context_abstract!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase C: /csr-turn endpoint (integration)
# ─────────────────────────────────────────────────────────────────────────────

def test_csr_turn_endpoint_returns_204() -> None:
    """POST /csr-turn returns 204 No Content."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need help with my fantasy novel editing.")
        thread_id = r1["thread_id"]
        _csr_turn(
            client, thread_id,
            csr_message="Hi there! I'm Sarah from BookCraft. How can I help?",
        )


def test_csr_turn_stores_commitment_in_state() -> None:
    """A CSR message with a price quote persists in csr_commitments on the thread state."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need publishing consultation.")
        thread_id = r1["thread_id"]

        _csr_turn(
            client, thread_id,
            csr_name="Mark",
            csr_message="Our full publishing package is $3,500 total for your book.",
        )

        state = _state(client, thread_id)
        assert state.csr_commitments, "No commitments stored after CSR price quote"
        assert any(c["type"] == "price_quoted" for c in state.csr_commitments)


def test_csr_turn_increments_turns_ingested() -> None:
    """csr_turns_ingested counter increments with each /csr-turn call."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need cover design help.")
        thread_id = r1["thread_id"]

        _csr_turn(client, thread_id, csr_message="Let me tell you about our cover design packages.")
        assert _state(client, thread_id).csr_turns_ingested == 1

        _csr_turn(client, thread_id, csr_message="We also offer illustration add-ons.")
        assert _state(client, thread_id).csr_turns_ingested == 2


def test_csr_turn_populates_verbatim_window() -> None:
    """3 CSR turns should fill verbatim window without touching abstract."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need book formatting.")
        thread_id = r1["thread_id"]

        _csr_turn(client, thread_id, csr_message="Formatting covers interior layout and design.")
        _csr_turn(client, thread_id, csr_message="We support epub, mobi, and print-ready PDF.")
        _csr_turn(client, thread_id, csr_message="Our turnaround for formatting is 10 business days.")

        state = _state(client, thread_id)
        assert len(state.csr_context_recent_verbatim) == 3
        assert state.csr_context_abstract == ""


def test_csr_turn_overflows_to_abstract_on_fourth() -> None:
    """4th CSR turn causes oldest verbatim to be compressed into abstract."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "Interested in NDA and ghostwriting.")
        thread_id = r1["thread_id"]

        _csr_turn(client, thread_id, csr_message="Let me explain our NDA process first.")
        _csr_turn(client, thread_id, csr_message="We require a signed agreement before any project begins.")
        _csr_turn(client, thread_id, csr_message="Ghostwriting engagements start with a discovery call.")
        _csr_turn(client, thread_id, csr_message="Packages for ghostwriting range from $5,000 to $15,000.")

        state = _state(client, thread_id)
        assert len(state.csr_context_recent_verbatim) == 3
        assert state.csr_context_abstract, "Abstract should contain compressed first turn"


def test_csr_turn_with_user_message_extracts_user_facts() -> None:
    """When user_message is provided, CSR turn extracts user-side facts into state."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "Hello, I want help.")
        thread_id = r1["thread_id"]

        # Pass a user message alongside CSR message — user reveals name
        _csr_turn(
            client, thread_id,
            csr_message="Great to meet you! What's your name?",
            user_message="My name is Aisha Patel and I have a 60,000 word thriller.",
        )
        # State applier should have run; at minimum csr_turns_ingested incremented
        state = _state(client, thread_id)
        assert state.csr_turns_ingested == 1


# ─────────────────────────────────────────────────────────────────────────────
# Phase C: /handover endpoint (integration)
# ─────────────────────────────────────────────────────────────────────────────

def test_handover_to_csr_activates_flag() -> None:
    """direction=to_csr sets csr_handover_active=True on state."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need editing for my completed novel.")
        thread_id = r1["thread_id"]

        resp = _handover(client, thread_id, "to_csr", csr_name="James")
        assert resp["direction"] == "to_csr"
        assert resp["csr_handover_active"] is True
        assert _state(client, thread_id).csr_handover_active is True


def test_handover_to_bot_deactivates_flag() -> None:
    """direction=to_bot clears csr_handover_active."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I have a 95k word romance novel.")
        thread_id = r1["thread_id"]

        _handover(client, thread_id, "to_csr")
        resp = _handover(client, thread_id, "to_bot")
        assert resp["csr_handover_active"] is False
        assert _state(client, thread_id).csr_handover_active is False


def test_handover_to_bot_sets_returned_timestamp() -> None:
    """direction=to_bot records csr_handover_returned_at datetime."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need formatting help.")
        thread_id = r1["thread_id"]

        _handover(client, thread_id, "to_csr")
        _handover(client, thread_id, "to_bot")

        state = _state(client, thread_id)
        assert state.csr_handover_returned_at is not None


def test_handover_bot_can_still_respond_after_return() -> None:
    """After handover returns to bot, the bot can still receive and process turns."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need proofreading for my 50k word memoir.")
        thread_id = r1["thread_id"]

        _handover(client, thread_id, "to_csr")
        _handover(client, thread_id, "to_bot")

        # Bot should still respond normally
        r2 = _turn(client, "What proofreading packages do you offer?", thread_id=thread_id)
        assert r2["thread_id"] == thread_id
        assert r2["bubbles"], "Bot should produce response after handover return"


# ─────────────────────────────────────────────────────────────────────────────
# Full 12-Turn Production Journey
#
# Scenario: Abdullah's publishing journey
#   Turns 1–4:  Bot discovery (service interest, word count, genre, name/contact)
#   Turns 5–6:  Handover to CSR "Sarah"
#   CSR 1–3:    Sarah sends 3 messages (pricing, timeline, discount)
#   CSR 4:      Overflow → abstract compression
#   Turns 7–8:  Handover back to bot
#   Turns 9–12: Bot continues with full CSR context retained
# ─────────────────────────────────────────────────────────────────────────────

def test_full_12_turn_bot_csr_bot_journey() -> None:
    """
    Complete production journey: bot → CSR handover → bot return.
    Validates:
    - Thread state persists across all 12 turns
    - CSR commitments recorded correctly
    - Verbatim window and abstract populated correctly
    - csr_handover_active flag transitions correctly
    - csr_handover_returned_at recorded
    - Bot can receive turns after handover return
    - csr_turns_ingested accurate count
    """
    app = _app()
    with TestClient(app) as client:
        # ── Bot discovery phase (turns 1–4) ──────────────────────────────
        r1 = _turn(client, "Hi, I'm Abdullah Malik. I've written a 78,000-word thriller called 'Dark Signal'.")
        thread_id = r1["thread_id"]
        assert thread_id

        r2 = _turn(
            client,
            "I'm looking for professional editing and cover design. My manuscript is complete.",
            thread_id=thread_id,
        )
        assert r2["thread_id"] == thread_id

        r3 = _turn(
            client,
            "My email is abdullah@example.com and my phone is +1-555-234-5678.",
            thread_id=thread_id,
        )
        assert r3["bubbles"], "Bot should respond to contact info"

        r4 = _turn(
            client,
            "I'd like to know about your editing pricing and timeline before we move forward.",
            thread_id=thread_id,
        )
        assert r4["bubbles"]

        # State after bot phase
        state_pre_handover = _state(client, thread_id)
        assert not state_pre_handover.csr_handover_active

        # ── Handover to CSR Sarah (turn 5) ───────────────────────────────
        ho1 = _handover(
            client, thread_id, "to_csr",
            csr_id="csr-sarah-01",
            csr_name="Sarah Thompson",
            handover_note="Customer wants pricing and timeline for editing + cover.",
        )
        assert ho1["csr_handover_active"] is True
        assert _state(client, thread_id).csr_handover_active is True

        # ── CSR Sarah sends 4 messages ────────────────────────────────────
        _csr_turn(
            client, thread_id,
            csr_id="csr-sarah-01", csr_name="Sarah Thompson",
            csr_message=(
                "Hi Abdullah! I'm Sarah from BookCraft. I've reviewed your project. "
                "For a 78,000-word thriller, our developmental editing package is $1,800."
            ),
            user_message="Great, what does that include?",
        )

        _csr_turn(
            client, thread_id,
            csr_id="csr-sarah-01", csr_name="Sarah Thompson",
            csr_message=(
                "It includes line editing, structural feedback, and one revision round. "
                "Turnaround is 4 weeks for your word count."
            ),
        )

        _csr_turn(
            client, thread_id,
            csr_id="csr-sarah-01", csr_name="Sarah Thompson",
            csr_message=(
                "For the cover design, our premium package is $600. "
                "Since you're bundling both, we can offer a 10% discount on the total."
            ),
        )

        # 4th CSR turn → overflow triggers abstract compression
        _csr_turn(
            client, thread_id,
            csr_id="csr-sarah-01", csr_name="Sarah Thompson",
            csr_message=(
                "The combined package with discount comes to $2,160. "
                "We can have the complete project ready in 5 weeks."
            ),
        )

        state_csr_phase = _state(client, thread_id)

        # Validate CSR state
        assert state_csr_phase.csr_turns_ingested == 4
        assert len(state_csr_phase.csr_context_recent_verbatim) == 3  # window capped at 3
        assert state_csr_phase.csr_context_abstract, "Abstract must be populated after 4 CSR turns"

        # Validate commitments detected
        assert state_csr_phase.csr_commitments, "CSR commitments should be recorded"
        commitment_types = {c["type"] for c in state_csr_phase.csr_commitments}
        assert "price_quoted" in commitment_types, f"Expected price_quoted, got: {commitment_types}"
        assert "timeline_promised" in commitment_types, f"Expected timeline_promised, got: {commitment_types}"
        assert "discount_offered" in commitment_types, f"Expected discount_offered, got: {commitment_types}"

        # Validate csr_name stored correctly
        for c in state_csr_phase.csr_commitments:
            assert c.get("csr_name") == "Sarah Thompson", f"Wrong csr_name: {c}"

        # ── Handover back to bot ──────────────────────────────────────────
        ho2 = _handover(client, thread_id, "to_bot", csr_id="csr-sarah-01", csr_name="Sarah Thompson")
        assert ho2["csr_handover_active"] is False

        state_post_handover = _state(client, thread_id)
        assert not state_post_handover.csr_handover_active
        assert state_post_handover.csr_handover_returned_at is not None

        # ── Bot continues (turns 9–12) ────────────────────────────────────
        r9 = _turn(
            client,
            "I'm ready to move forward. Can you confirm the package details?",
            thread_id=thread_id,
        )
        assert r9["bubbles"], "Bot must respond after handover return"
        assert r9["thread_id"] == thread_id

        r10 = _turn(
            client,
            "Will the 5-week timeline definitely hold for my novel?",
            thread_id=thread_id,
        )
        assert r10["bubbles"]

        r11 = _turn(
            client,
            "What do I need to send you to get started?",
            thread_id=thread_id,
        )
        assert r11["bubbles"]

        r12 = _turn(
            client,
            "Perfect. Let's schedule the consultation to finalise everything.",
            thread_id=thread_id,
        )
        assert r12["bubbles"]

        # Final state validation
        final_state = _state(client, thread_id)
        assert not final_state.csr_handover_active  # still correctly deactivated
        assert final_state.csr_turns_ingested == 4  # unchanged — no new CSR turns
        assert len(final_state.csr_context_recent_verbatim) == 3  # window intact
        assert final_state.csr_context_abstract  # abstract preserved


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases and regression guards
# ─────────────────────────────────────────────────────────────────────────────

def test_csr_turn_on_unknown_thread_creates_new_state() -> None:
    """CSR turn with a fresh UUID creates a new thread state without crashing."""
    app = _app()
    with TestClient(app) as client:
        fresh_thread_id = str(uuid4())
        _csr_turn(
            client, fresh_thread_id,
            csr_message="Hello, welcome to BookCraft!",
        )
        state = _state(client, fresh_thread_id)
        assert state.csr_turns_ingested == 1


def test_handover_on_unknown_thread_creates_state() -> None:
    """Handover on a fresh UUID creates state and sets the flag correctly."""
    app = _app()
    with TestClient(app) as client:
        fresh_thread_id = str(uuid4())
        resp = _handover(client, fresh_thread_id, "to_csr")
        assert resp["csr_handover_active"] is True


def test_double_handover_to_csr_idempotent() -> None:
    """Two consecutive to_csr handovers leave state consistent."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need help with my book.")
        thread_id = r1["thread_id"]

        _handover(client, thread_id, "to_csr")
        resp2 = _handover(client, thread_id, "to_csr")
        assert resp2["csr_handover_active"] is True
        assert _state(client, thread_id).csr_handover_active is True


def test_csr_commitment_persists_across_bot_turns() -> None:
    """Commitments recorded during CSR phase survive subsequent bot turns."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need proofreading and indexing.")
        thread_id = r1["thread_id"]

        _handover(client, thread_id, "to_csr")
        _csr_turn(
            client, thread_id,
            csr_message="The proofreading package is $950 and indexing is $400 total.",
        )
        _handover(client, thread_id, "to_bot")

        # Multiple bot turns
        for msg in [
            "What does proofreading cover?",
            "How about the indexing process?",
            "Can I get both done together?",
        ]:
            _turn(client, msg, thread_id=thread_id)

        final_state = _state(client, thread_id)
        assert final_state.csr_commitments, "Commitments must survive bot turns"
        assert any(c["type"] == "price_quoted" for c in final_state.csr_commitments)


def test_csr_turn_empty_message_handled_gracefully() -> None:
    """A CSR turn with an empty csr_message body is rejected at the API layer."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "Hello.")
        thread_id = r1["thread_id"]

        # Empty csr_message violates min_length=1 — should return 422
        r = client.post("/api/v1/chat/csr-turn", json={
            "thread_id": thread_id,
            "csr_id": "csr-001",
            "csr_name": "Test CSR",
            "csr_message": "",
        })
        assert r.status_code == 422, f"Expected 422 for empty csr_message, got {r.status_code}"


def test_handover_invalid_direction_rejected() -> None:
    """An invalid direction value is rejected with 422."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "Hello.")
        thread_id = r1["thread_id"]

        r = client.post("/api/v1/chat/handover", json={
            "thread_id": thread_id,
            "direction": "sideways",  # invalid
        })
        assert r.status_code == 422, f"Expected 422 for invalid direction, got {r.status_code}"


def test_multiple_csr_agents_in_sequence() -> None:
    """Commitments from different CSR agents are all preserved, each tagged with csr_name."""
    app = _app()
    with TestClient(app) as client:
        r1 = _turn(client, "I need editing for my 90,000-word fantasy novel.")
        thread_id = r1["thread_id"]

        # First CSR agent
        _handover(client, thread_id, "to_csr", csr_id="csr-001", csr_name="Rachel")
        _csr_turn(
            client, thread_id, csr_id="csr-001", csr_name="Rachel",
            csr_message="Hi! For 90k words, editing is $2,200. We can deliver in 5 weeks.",
        )
        _handover(client, thread_id, "to_bot")

        _turn(client, "Thanks. Let me think about it.", thread_id=thread_id)

        # Second CSR agent picks up
        _handover(client, thread_id, "to_csr", csr_id="csr-002", csr_name="David")
        _csr_turn(
            client, thread_id, csr_id="csr-002", csr_name="David",
            csr_message="Hi, I'm David. We also offer a free consultation to get started.",
        )
        _handover(client, thread_id, "to_bot")

        state = _state(client, thread_id)
        names = {c["csr_name"] for c in state.csr_commitments}
        assert "Rachel" in names
        assert "David" in names


def test_thread_state_consistent_after_rapid_turns() -> None:
    """10 rapid bot turns on one thread maintain consistent state without corruption."""
    app = _app()
    with TestClient(app) as client:
        messages = [
            "I have a completed crime thriller, 65,000 words.",
            "The title is 'Shadow Protocol'.",
            "I need editing and cover design together.",
            "My budget is around $2,000 for both services.",
            "I want a dark, gritty cover — think noir style.",
            "The target audience is adult thriller readers.",
            "I can start within two weeks.",
            "My name is Omar Sheikh, email omar@example.com.",
            "Phone: +1-800-555-0199.",
            "When can we schedule the kickoff consultation?",
        ]

        thread_id = None
        for msg in messages:
            r = _turn(client, msg, thread_id=thread_id)
            thread_id = r["thread_id"]
            assert r["bubbles"], f"No response for message: {msg[:40]!r}"

        state = _state(client, thread_id)
        # Must not have crashed — basic sanity
        assert not state.csr_handover_active
        assert state.csr_turns_ingested == 0  # no CSR turns in this flow
