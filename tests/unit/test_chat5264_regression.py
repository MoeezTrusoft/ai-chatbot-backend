"""Regression tests for Chat 5264 (The Solarian Chronicles) issues.

Issue map:
  1  — "very much human" persona phrasing removed from generator
  2  — RAG document-body bleed detected by quality gate (no-asterisk pattern)
  2b — System prompt instructs LLM not to copy RAG verbatim
  3  — Manuscript status extraction: lore docs, chapter summary, partially outlined
  4  — Word count extraction: hedged estimates + correction
  5/7 — Sequential queue inter-turn delay raised to 2 000 ms
  8  — Consultation CTA not re-asked after customer affirms interest
  9  — preferred_contact_method extraction for email/phone preference
"""

from __future__ import annotations

import re

import pytest

from bookcraft.components.context import ContextPackBuilder
from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.extraction.llm_extractor import _EXTRACTION_SYSTEM
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.components.extraction.llm_extractor import _facts_to_deltas
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.extraction.state_applier import StateApplier
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.generator import _response_system_prompt as _build_response_system_prompt
from bookcraft.components.response.quality_gate import ResponseQualityGate
from bookcraft.domain.enums import QueryIntentType, SalesStage, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState

gate = ResponseQualityGate()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(query: QueryIntentType = QueryIntentType.SERVICE_QUESTION) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=None,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.90,
        rationale="test",
        evidence=[],
    )


def _field(value, *, confidence: float = 0.92):
    return FieldMeta(value=value, confidence=confidence, source=Source.AI_EXTRACTED)


def _system_prompt(state: ThreadState | None = None) -> str:
    return _build_response_system_prompt(active_service=None, persona_decision=None)


def _evaluate(text: str, query: QueryIntentType = QueryIntentType.SERVICE_QUESTION) -> object:
    return gate.evaluate(text=text, intent=_intent(query), state=ThreadState())


# ---------------------------------------------------------------------------
# Issue 1 — "Very much human" and identity phrasing
# ---------------------------------------------------------------------------

class TestPersonaPhrasing:
    def test_system_prompt_forbids_very_much_human(self):
        """The system prompt must FORBID 'very much human' phrasing, not use it as output."""
        src = _system_prompt()
        lowered = src.lower()
        # The phrase appears ONLY inside a prohibition ("never", "do not", "avoid").
        idx = lowered.find("very much human")
        if idx != -1:
            # Look in a wider window (80 chars) to catch "never describe yourself as ... 'very much human'"
            surrounding = lowered[max(0, idx - 80): idx + 40]
            assert any(kw in surrounding for kw in ("never", "not", "avoid", "do not")), (
                f"'very much human' appears outside a prohibition context: {surrounding!r}"
            )

    def test_system_prompt_no_real_person_not_a_bot(self):
        src = _system_prompt()
        assert "real person, not a bot" not in src.lower()
        assert "not a bot or ai system" not in src.lower()

    def test_system_prompt_no_you_are_a_human_representative(self):
        src = _system_prompt()
        # "You are a human representative." should no longer appear
        assert "you are a human representative" not in src.lower()

    def test_identity_forbidden_phrases_documented_in_generator_source(self):
        import inspect
        from bookcraft.components.response import generator as gen
        src = inspect.getsource(gen)
        # The source must forbid "very much human" phrasing
        assert "very much human" in src

    def test_persona_still_identifies_as_bookcraft(self):
        """The bot should still identify as a BookCraft consultant, just without AI/human claims."""
        src = _system_prompt()
        assert "bookcraft" in src.lower()


# ---------------------------------------------------------------------------
# Issue 2 — RAG document-body bleed detection (no asterisks)
# ---------------------------------------------------------------------------

class TestRagDocumentBodyBleed:
    """The specific RAG text from Chat 5264 must now fail the quality gate."""

    RAG_BLEED_TEXT = (
        "What Influences Cost & Timeline Beyond genre and engagement model, these factors "
        "can affect your quote - particularly important for fiction with extensive worldbuilding, "
        "series planning, or specialized non-fiction: Content complexity drivers - Advanced "
        "technical or specialized content - Multi-perspe."
    )

    def test_rag_bleed_triggers_markdown_formatting_failure(self):
        result = _evaluate(self.RAG_BLEED_TEXT)
        assert not result.passed
        assert any("markdown" in f for f in result.failures)

    def test_what_influences_cost_pattern_detected(self):
        result = _evaluate(
            "What Influences Cost & Timeline Beyond genre and engagement model, "
            "these factors can affect your quote."
        )
        assert not result.passed

    def test_content_complexity_drivers_detected(self):
        result = _evaluate(
            "Content complexity drivers - Advanced technical or specialized content."
        )
        assert not result.passed

    def test_normal_pricing_prose_passes(self):
        """Legitimate pricing-related prose must not be flagged."""
        result = _evaluate(
            "Pricing depends on scope and service — a free consultation gives you an accurate quote."
        )
        assert result.passed

    def test_ghostwriting_service_answer_passes(self):
        result = _evaluate(
            "Ghostwriting is exactly what you need here. "
            "A specialist can give you a tailored quote after a quick call."
        )
        assert result.passed

    def test_system_prompt_forbids_verbatim_rag_copy(self):
        src = _system_prompt()
        assert "never copy rag text verbatim" in src.lower() or "never copy" in src.lower()


# ---------------------------------------------------------------------------
# Issue 3 — Manuscript status: lore docs / chapter summary / partially outlined
# ---------------------------------------------------------------------------

class TestManuscriptStatusExtraction5264:
    """Phrases from Chat 5264 that previously failed to extract manuscript_status."""

    def test_rule8_contains_partially_outlined(self):
        assert "partially outlined" in _EXTRACTION_SYSTEM.lower()

    def test_rule8_contains_lore_written(self):
        assert "lore" in _EXTRACTION_SYSTEM.lower()

    def test_rule8_contains_chapter_summary(self):
        assert "chapter summary" in _EXTRACTION_SYSTEM.lower() or "summary" in _EXTRACTION_SYSTEM.lower()

    def test_rule8_contains_just_ideas(self):
        assert "ideas" in _EXTRACTION_SYSTEM.lower()

    def test_lore_docs_maps_to_notes_only(self):
        """Simulate what LLM should extract for 'I have lore already written out'."""
        facts = LLMExtractedFacts(
            manuscript_status=ExtractedValue(
                value="notes_only",
                confidence=0.92,
                source_quote="I have a lot of lore already written out and some random scenes",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert deltas[0].path == "project.manuscript_status"
        assert deltas[0].value == "notes_only"
        assert deltas[0].confidence == 0.92

    def test_chapter_summary_maps_to_notes_only(self):
        facts = LLMExtractedFacts(
            manuscript_status=ExtractedValue(
                value="notes_only",
                confidence=0.92,
                source_quote="I have a summary of what could be the first couple of chapters",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert deltas[0].value == "notes_only"

    def test_partially_outlined_maps_to_notes_only(self):
        facts = LLMExtractedFacts(
            manuscript_status=ExtractedValue(
                value="notes_only",
                confidence=0.92,
                source_quote="partially outlined",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert deltas[0].value == "notes_only"

    def test_pack_suppresses_manuscript_stage_after_notes_only_set(self):
        state = ThreadState()
        state.project.manuscript_status = _field("notes_only")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" not in pack.missing_facts
        assert "manuscript_stage" in pack.forbidden_reasks


# ---------------------------------------------------------------------------
# Issue 4 — Word count: hedged estimates and corrections
# ---------------------------------------------------------------------------

class TestWordCountExtraction5264:
    def test_rule10_present_in_extraction_prompt(self):
        assert "around 130,000" in _EXTRACTION_SYSTEM or "hedged" in _EXTRACTION_SYSTEM.lower()

    def test_hedged_word_count_fills_empty_field(self):
        """'around 130,000' → confidence 0.70 → downscaled to 0.3 fill-only → fills empty field."""
        facts = LLMExtractedFacts(
            word_count=ExtractedValue(value=130000, confidence=0.70, source_quote="around 130,000")
        )
        deltas = _facts_to_deltas(facts)
        assert deltas[0].path == "project.word_count"
        assert deltas[0].value == 130000
        assert deltas[0].confidence == 0.3  # downscaled; fills empty field

    def test_correction_overwrites_prior_hedged_value(self):
        """'100,000 sounds more reasonable' → confidence 0.90 → overwrites prior 0.3."""
        applier = StateApplier()
        state = ThreadState()
        # Apply initial hedged value (confidence 0.3 fill-only)
        state = applier.apply(state, CombinedExtraction(state_deltas=[
            StateDelta(path="project.word_count", value=130000, confidence=0.3,
                       source=Source.AI_EXTRACTED, extracted_by="test")
        ]))
        assert state.project.word_count.value == 130000
        assert state.project.word_count.confidence == 0.3

        # Apply correction (confidence 0.90 → above 0.3 → overwrites)
        facts = LLMExtractedFacts(
            word_count=ExtractedValue(value=100000, confidence=0.90,
                                      source_quote="100,000 sounds more reasonable")
        )
        deltas = _facts_to_deltas(facts)
        state = applier.apply(state, CombinedExtraction(state_deltas=deltas))
        assert state.project.word_count.value == 100000

    def test_word_count_in_missing_facts_when_absent(self):
        state = ThreadState()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "word_or_page_count" in pack.missing_facts

    def test_word_count_suppressed_from_missing_when_set(self):
        state = ThreadState()
        state.project.word_count = _field(100000)
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "word_or_page_count" not in pack.missing_facts


# ---------------------------------------------------------------------------
# Issue 8 — Consultation CTA not re-asked after customer affirms interest
# ---------------------------------------------------------------------------

class TestConsultationCtaSuppression:
    def _pack_with_stage(self, stage: str) -> ContextPack:
        state = ThreadState()
        state.consultation_stage = stage  # type: ignore[attr-defined]
        return ContextPackBuilder().build(state=state, intent=_intent())

    def test_consultation_interest_forbidden_when_stage_pending(self):
        pack = self._pack_with_stage("consultation_pending")
        assert "consultation_interest" in pack.forbidden_reasks

    def test_consultation_interest_forbidden_when_stage_time_requested(self):
        pack = self._pack_with_stage("consultation_time_requested")
        assert "consultation_interest" in pack.forbidden_reasks

    def test_consultation_offer_forbidden_when_stage_set(self):
        pack = self._pack_with_stage("consultation_pending")
        assert "consultation_offer" in pack.forbidden_reasks

    def test_consultation_interest_NOT_forbidden_when_stage_absent(self):
        """When no consultation stage is set, CTA is allowed (first offer)."""
        state = ThreadState()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "consultation_interest" not in pack.forbidden_reasks

    def test_quality_gate_catches_cta_reask_when_forbidden(self):
        """Quality gate must flag 'Would you like a consultation?' when it's forbidden."""
        pack = self._pack_with_stage("consultation_pending")
        result = gate.evaluate(
            text="Would you like to connect with a BookCraft specialist for a free consultation?",
            intent=_intent(),
            state=ThreadState(),
            context_pack=pack,
        )
        assert not result.passed
        assert any("reask" in f.lower() or "forbidden" in f.lower() for f in result.failures)

    def test_legitimate_consultation_invite_passes_before_stage_set(self):
        state = ThreadState()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        result = gate.evaluate(
            text="Would you like to connect with a BookCraft specialist for a free consultation?",
            intent=_intent(),
            state=state,
            context_pack=pack,
        )
        # No forbidden_reask violation because stage is not set yet
        assert not any("consultation_interest" in f for f in result.failures)


# ---------------------------------------------------------------------------
# Issue 9 — Preferred contact method extraction
# ---------------------------------------------------------------------------

class TestPreferredContactExtraction:
    def test_rule11_present_in_extraction_prompt(self):
        assert "preferred_contact_method" in _EXTRACTION_SYSTEM.lower()
        assert "prefer email" in _EXTRACTION_SYSTEM.lower() or "prefer" in _EXTRACTION_SYSTEM.lower()

    def test_email_preference_extracted(self):
        facts = LLMExtractedFacts(
            preferred_contact_method=ExtractedValue(
                value="email",
                confidence=0.92,
                source_quote="i'd prefer email if they can",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert deltas[0].path == "personal.preferred_contact_method"
        assert deltas[0].value == "email"

    def test_phone_preference_extracted(self):
        facts = LLMExtractedFacts(
            preferred_contact_method=ExtractedValue(
                value="phone",
                confidence=0.92,
                source_quote="please call me",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert deltas[0].path == "personal.preferred_contact_method"
        assert deltas[0].value == "phone"

    def test_preferred_contact_in_known_facts_when_set(self):
        state = ThreadState()
        state.personal.preferred_contact_method = _field("email")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.preferred_contact_method" in paths


# ---------------------------------------------------------------------------
# Issue 5/7 — Sequential queue inter-turn delay is 2 000 ms
# ---------------------------------------------------------------------------

class TestSequentialQueueDelay:
    def test_sequential_queue_delay_is_2000ms(self):
        """Verify socket.js uses 2000 ms not 50 ms between sequential AI turns."""
        socket_path = (
            "/Users/mac/Desktop/Abdullah/bookcraft-node-chatbot/"
            "csr-trusoft-node/src/config/socket.js"
        )
        with open(socket_path) as f:
            src = f.read()
        # Must contain 2000 in the sequential queue context, not 50
        assert "runAiTurnSequential" in src
        # The 50ms delay should no longer be present next to runAiTurnSequential
        # Find the setTimeout call for runAiTurnSequential
        pattern = re.compile(
            r'setTimeout\s*\(\s*\(\)\s*=>\s*runAiTurnSequential[^)]+\),\s*(\d+)',
            re.DOTALL,
        )
        match = pattern.search(src)
        assert match, "Could not find setTimeout for runAiTurnSequential"
        delay_value = int(match.group(1))
        assert delay_value == 2000, f"Expected 2000ms delay, got {delay_value}ms"


# ---------------------------------------------------------------------------
# Full integration: Solarian Chronicles scenario simulation
# ---------------------------------------------------------------------------

class TestSolarianChroniclesScenario:
    """High-level integration checks simulating Chat 5264 state progression."""

    def _state_after_contact_given(self) -> ThreadState:
        state = ThreadState()
        state.personal.email = _field("Lbranan99@gmail.com")
        return state

    def _state_after_manuscript_info(self) -> ThreadState:
        state = self._state_after_contact_given()
        state.project.manuscript_status = _field("notes_only")
        state.project.word_count = _field(100000)
        state.project.genre = _field("epic fantasy / science fiction")
        return state

    def test_manuscript_not_in_missing_after_lore_declared(self):
        state = self._state_after_manuscript_info()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" not in pack.missing_facts

    def test_word_count_not_in_missing_after_given(self):
        state = self._state_after_manuscript_info()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "word_or_page_count" not in pack.missing_facts

    def test_genre_not_in_missing_after_epic_fantasy(self):
        state = self._state_after_manuscript_info()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "genre" not in pack.missing_facts

    def test_consultation_cta_suppressed_after_customer_says_yes(self):
        state = self._state_after_manuscript_info()
        state.consultation_stage = "consultation_pending"  # type: ignore[attr-defined]
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "consultation_interest" in pack.forbidden_reasks

    def test_email_in_known_facts(self):
        state = self._state_after_contact_given()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.email" in paths

    def test_email_in_forbidden_reasks(self):
        state = self._state_after_contact_given()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "email" in pack.forbidden_reasks
