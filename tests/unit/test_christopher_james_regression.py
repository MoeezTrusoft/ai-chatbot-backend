"""Regression tests for the Christopher James (Chat 5148) bugs.

Three bugs diagnosed:
  1. LLM extractor vocabulary for manuscript_status — "started from scratch", "in my head",
     "first time on paper" were never mapped → extraction returned null → bot re-asked.
  2. generator.py missing_str built from state directly, ignoring context_pack.forbidden_reasks
     → "manuscript stage" shown as still-needed even when forbidden → LLM re-asked.
  3. planner.py always returned "name_and_email_or_phone" when contact incomplete, even when
     name was already captured → consultation flow re-asked for name.
"""

from __future__ import annotations

import pytest

from bookcraft.components.context import ContextPackBuilder
from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.extraction.llm_extractor import (
    LLMExtractionResult,
    LLMMetadataExtractor,
    _EXTRACTION_SYSTEM,
    _build_known_facts_block,
    _facts_to_deltas,
)
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.leads import ContactCaptureResult, LeadObjectiveDecision
from bookcraft.components.response.generator import _response_user_prompt
from bookcraft.components.response.planner import ResponsePlanner, _contact_next_question
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ServiceInterest, ThreadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_msg(text: str) -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text,
        language="en",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[],
        char_count=len(text),
    )


def _field(value, *, confidence: float = 0.92):
    return FieldMeta(value=value, confidence=confidence, source=Source.AI_EXTRACTED)


def _intent(
    *,
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: ServiceCategory | None = None,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.90,
        rationale="test",
        evidence=[],
    )


def _pack_with_known_facts(known_facts: list[KnownFact], *, forbidden: list[str] | None = None) -> ContextPack:
    return ContextPack(
        known_facts=known_facts,
        missing_facts=[],
        forbidden_reasks=forbidden or [],
        allowed_next_questions=[],
    )


def _known_fact(path: str, label: str, value: str) -> KnownFact:
    return KnownFact(path=path, label=label, value=value, confidence=0.92, source="ai_extracted")


def _lead_stop(next_question: str | None = None) -> LeadObjectiveDecision:
    return LeadObjectiveDecision(
        stage="contact_requested",
        stop_discovery=True,
        objective_move="ask_contact",
        next_question=next_question,
        recommended_primary_goal="lead_contact_capture",
        reason="test: stop discovery and collect contact",
    )


def _contact_capture_not_ready() -> ContactCaptureResult:
    from bookcraft.components.leads.contact import ContactInfo
    return ContactCaptureResult(
        contact=ContactInfo(),
        has_name=False,
        has_email=False,
        has_phone=False,
        lead_contact_ready=False,
        contact_complete=False,
    )


# ---------------------------------------------------------------------------
# Bug 1 — Extractor vocabulary for manuscript_status
# ---------------------------------------------------------------------------

class TestExtractionVocabulary:
    """Verify the extraction system prompt contains the LLM-driven manuscript_status rules."""

    def test_system_prompt_contains_not_started_key(self):
        assert "not_started" in _EXTRACTION_SYSTEM

    def test_system_prompt_contains_scratch_concept(self):
        # New LLM-driven approach uses "Starting from scratch" as a capitalised example.
        assert "scratch" in _EXTRACTION_SYSTEM.lower()

    def test_system_prompt_contains_in_my_head_concept(self):
        # New approach uses "Still in my head" as a guidance example.
        assert "in my head" in _EXTRACTION_SYSTEM or "in their head" in _EXTRACTION_SYSTEM

    def test_system_prompt_contains_chapter_example(self):
        # New approach uses "I have 5 chapters" as an example for early_draft.
        assert "chapters" in _EXTRACTION_SYSTEM.lower()

    def test_system_prompt_contains_notes_only_key(self):
        assert "notes_only" in _EXTRACTION_SYSTEM

    def test_system_prompt_contains_early_draft_key(self):
        assert "early_draft" in _EXTRACTION_SYSTEM

    def test_system_prompt_contains_full_draft_key(self):
        assert "full_draft" in _EXTRACTION_SYSTEM

    def test_system_prompt_contains_name_typo_normalization(self):
        assert "typo" in _EXTRACTION_SYSTEM.lower() or "normalize" in _EXTRACTION_SYSTEM.lower()

    def test_high_confidence_threshold_documented_for_manuscript(self):
        assert "0.92" in _EXTRACTION_SYSTEM

    def test_facts_to_deltas_maps_manuscript_status_correctly(self):
        """A not_started extraction must produce a StateDelta at high confidence."""
        facts = LLMExtractedFacts(
            manuscript_status=ExtractedValue(
                value="not_started",
                confidence=0.92,
                source_quote="I started from scratch",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert len(deltas) == 1
        d = deltas[0]
        assert d.path == "project.manuscript_status"
        assert d.value == "not_started"
        assert d.confidence == 0.92  # above gate → stored at full confidence

    def test_low_confidence_extraction_downscaled(self):
        """Hedged manuscript extraction goes through the 0.3 fill gate."""
        facts = LLMExtractedFacts(
            manuscript_status=ExtractedValue(
                value="maybe notes",
                confidence=0.70,
                source_quote="I think I have some notes",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert len(deltas) == 1
        assert deltas[0].confidence == 0.3  # downscaled to fill-only

    def test_name_normalization_field_mapped_correctly(self):
        """A normalized name extraction (typo corrected) should map to personal.name."""
        facts = LLMExtractedFacts(
            name=ExtractedValue(
                value="Christopher James",  # typo "Chri9stopher" → normalized
                confidence=0.92,
                source_quote="Chri9stopher James",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert len(deltas) == 1
        assert deltas[0].path == "personal.name"
        assert deltas[0].value == "Christopher James"
        assert deltas[0].confidence == 0.92


# ---------------------------------------------------------------------------
# Bug 2 — generator.py missing_str must respect context_pack.forbidden_reasks
# ---------------------------------------------------------------------------

class TestGeneratorMissingStr:
    """The 'What we still need' line in the user prompt must not list manuscript stage
    when the context pack's forbidden_reasks already suppresses it."""

    def _build_prompt(self, state: ThreadState, context_pack: ContextPack) -> str:
        msg = _make_msg("I started from scratch")
        return _response_user_prompt(
            message=msg,
            state=state,
            intent=_intent(),
            extraction=CombinedExtraction(state_deltas=[]),
            rag_chunks=[],
            route_name="service_question",
            runtime_atoms={},
            context_pack=context_pack,
        )

    def test_manuscript_stage_absent_from_missing_when_forbidden(self):
        """When manuscript_stage is in forbidden_reasks, 'manuscript stage' must NOT
        appear in 'What we still need'."""
        state = ThreadState()  # no manuscript_status in state
        pack = ContextPack(
            known_facts=[],
            missing_facts=["genre"],  # manuscript_stage deliberately not in missing_facts
            forbidden_reasks=["manuscript_stage", "manuscript_status", "manuscript stage"],
            allowed_next_questions=["genre"],
        )
        prompt = self._build_prompt(state, pack)
        # The missing section should not list manuscript stage.
        assert "What we still need" in prompt
        missing_line = [l for l in prompt.splitlines() if "still need" in l.lower()]
        assert missing_line, "Prompt must contain 'What we still need' line"
        assert "manuscript" not in missing_line[0].lower()

    def test_manuscript_stage_listed_when_not_forbidden_and_empty(self):
        """When manuscript_status is not in state AND not forbidden, it should appear
        as a missing fact (baseline correctness)."""
        state = ThreadState()  # no manuscript_status
        pack = ContextPack(
            known_facts=[],
            missing_facts=["manuscript_stage"],
            forbidden_reasks=[],
            allowed_next_questions=["manuscript_stage"],
        )
        prompt = self._build_prompt(state, pack)
        missing_line = [l for l in prompt.splitlines() if "still need" in l.lower()]
        assert missing_line
        assert "manuscript" in missing_line[0].lower()

    def test_name_shown_in_known_when_in_state(self):
        """When name is in state, 'What we already know' must include 'author name'."""
        state = ThreadState()
        state.personal.name = _field("Christopher James")
        pack = ContextPack(
            known_facts=[_known_fact("personal.name", "author_name", "Christopher James")],
            missing_facts=[],
            forbidden_reasks=["name", "author_name", "your name"],
            allowed_next_questions=[],
        )
        prompt = self._build_prompt(state, pack)
        known_line = [l for l in prompt.splitlines() if "already know" in l.lower()]
        assert known_line, "Prompt must contain 'What we already know' line"
        assert "christopher james" in known_line[0].lower()

    def test_email_shown_in_known_when_in_state(self):
        """When email is in state, 'What we already know' must include 'author email'."""
        state = ThreadState()
        state.personal.email = _field("cjames@yahoo.com")
        pack = ContextPack(
            known_facts=[_known_fact("personal.email", "author_email", "cjames@yahoo.com")],
            missing_facts=[],
            forbidden_reasks=["email", "author_email"],
            allowed_next_questions=[],
        )
        prompt = self._build_prompt(state, pack)
        known_line = [l for l in prompt.splitlines() if "already know" in l.lower()]
        assert known_line
        assert "author email" in known_line[0].lower()

    def test_phone_shown_in_known_when_in_state(self):
        """When phone is in state, 'What we already know' must include 'author phone'."""
        state = ThreadState()
        state.personal.phone = _field("337-251-3162")
        pack = ContextPack(
            known_facts=[_known_fact("personal.phone", "author_phone", "337-251-3162")],
            missing_facts=[],
            forbidden_reasks=["phone", "author_phone"],
            allowed_next_questions=[],
        )
        prompt = self._build_prompt(state, pack)
        known_line = [l for l in prompt.splitlines() if "already know" in l.lower()]
        assert known_line
        assert "author phone" in known_line[0].lower()


# ---------------------------------------------------------------------------
# Bug 3 — planner must not ask for name when it's already captured
# ---------------------------------------------------------------------------

class TestContactNextQuestion:
    """_contact_next_question must return the most specific question given captured facts."""

    def test_nothing_captured_returns_name_and_email_or_phone(self):
        pack = _pack_with_known_facts([])
        assert _contact_next_question(pack) == "name_and_email_or_phone"

    def test_name_captured_returns_missing_phone(self):
        """When name is known but phone is not, ask for phone (phone is required)."""
        pack = _pack_with_known_facts([
            _known_fact("personal.name", "author_name", "Christopher James"),
        ])
        assert _contact_next_question(pack) == "missing_phone"

    def test_name_and_email_captured_returns_missing_phone(self):
        """When name and email are known but phone is not, ask for phone."""
        pack = _pack_with_known_facts([
            _known_fact("personal.name", "author_name", "Christopher James"),
            _known_fact("personal.email", "author_email", "cj@example.com"),
        ])
        assert _contact_next_question(pack) == "missing_phone"

    def test_name_and_phone_captured_no_email_returns_missing_email(self):
        """When name and phone are known but email is not, ask for email."""
        pack = _pack_with_known_facts([
            _known_fact("personal.name", "author_name", "Christopher James"),
            _known_fact("personal.phone", "author_phone", "555-1234"),
        ])
        assert _contact_next_question(pack) == "missing_email"

    def test_all_three_captured_returns_preferred_call_time(self):
        """When all contact fields are present, fall back to call-time question."""
        pack = _pack_with_known_facts([
            _known_fact("personal.name", "author_name", "Christopher James"),
            _known_fact("personal.email", "author_email", "cj@example.com"),
            _known_fact("personal.phone", "author_phone", "555-1234"),
        ])
        assert _contact_next_question(pack) == "preferred_call_time"

    def test_planner_uses_smart_contact_question_not_name_when_name_known(self):
        """The planner's _next_question must route to missing_phone, not name_and_email_or_phone,
        when name is already in known_facts and lead_objective says stop_discovery.
        Phone is required; email is optional."""
        planner = ResponsePlanner()
        pack = _pack_with_known_facts(
            [_known_fact("personal.name", "author_name", "Christopher James")],
            forbidden=["name", "author_name", "your name"],
        )
        plan = planner.plan(
            intent=_intent(query=QueryIntentType.PRICING_QUESTION),
            state=ThreadState(),
            context_pack=pack,
            lead_objective_decision=_lead_stop(next_question=None),
            contact_capture_result=_contact_capture_not_ready(),
        )
        # Must NOT ask for name again.
        assert plan.next_question != "name_and_email_or_phone"
        assert plan.next_question == "missing_phone"

    def test_planner_asks_name_when_name_not_captured(self):
        """When name is absent from known_facts, planner must ask for name."""
        planner = ResponsePlanner()
        pack = _pack_with_known_facts([])
        plan = planner.plan(
            intent=_intent(query=QueryIntentType.PRICING_QUESTION),
            state=ThreadState(),
            context_pack=pack,
            lead_objective_decision=_lead_stop(next_question=None),
            contact_capture_result=_contact_capture_not_ready(),
        )
        assert plan.next_question == "name_and_email_or_phone"


# ---------------------------------------------------------------------------
# Bug 1+2 integration — pack_builder forbidden_reasks covers manuscript phrases
# ---------------------------------------------------------------------------

class TestPackBuilderManuscriptForbiddenReasks:
    """When manuscript_status is known in state, pack_builder must add broad phrase coverage
    to forbidden_reasks so the LLM cannot ask using any natural rephrasing."""

    def _build_pack_with_manuscript(self, value: str) -> ContextPack:
        state = ThreadState()
        state.project.manuscript_status = _field(value)
        return ContextPackBuilder().build(state=state, intent=_intent())

    def test_manuscript_stage_in_forbidden(self):
        pack = self._build_pack_with_manuscript("not_started")
        assert "manuscript_stage" in pack.forbidden_reasks

    def test_manuscript_status_in_forbidden(self):
        pack = self._build_pack_with_manuscript("not_started")
        assert "manuscript_status" in pack.forbidden_reasks

    def test_manuscript_stage_phrase_in_forbidden(self):
        pack = self._build_pack_with_manuscript("not_started")
        assert "manuscript stage" in pack.forbidden_reasks

    def test_do_you_have_a_manuscript_in_forbidden(self):
        pack = self._build_pack_with_manuscript("not_started")
        assert "do you have a manuscript" in pack.forbidden_reasks

    def test_have_you_started_in_forbidden(self):
        pack = self._build_pack_with_manuscript("not_started")
        assert "have you started" in pack.forbidden_reasks

    def test_manuscript_stand_phrase_in_forbidden(self):
        """Covers 'where does the manuscript stand' phrasing from transcript."""
        pack = self._build_pack_with_manuscript("not_started")
        assert "manuscript stand" in pack.forbidden_reasks

    def test_manuscript_not_in_forbidden_when_unknown(self):
        """When manuscript_status is not set, it should be in missing_facts, not forbidden."""
        state = ThreadState()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" in pack.missing_facts
        assert "manuscript_stage" not in pack.forbidden_reasks

    def test_manuscript_not_in_missing_when_known(self):
        """When manuscript_status is set, it must NOT appear in missing_facts."""
        pack = self._build_pack_with_manuscript("not_started")
        assert "manuscript_stage" not in pack.missing_facts


# ---------------------------------------------------------------------------
# Contact re-ask regression — pack_builder must surface name/email/phone in known_facts
# ---------------------------------------------------------------------------

class TestPackBuilderContactKnownFacts:
    """pack_builder must add personal contact fields to known_facts and forbidden_reasks."""

    def test_name_in_known_facts_when_in_state(self):
        state = ThreadState()
        state.personal.name = _field("Christopher James")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.name" in paths

    def test_email_in_known_facts_when_in_state(self):
        state = ThreadState()
        state.personal.email = _field("cj@example.com")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.email" in paths

    def test_phone_in_known_facts_when_in_state(self):
        state = ThreadState()
        state.personal.phone = _field("337-251-3162")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.phone" in paths

    def test_name_in_forbidden_reasks_when_in_state(self):
        state = ThreadState()
        state.personal.name = _field("Christopher James")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "name" in pack.forbidden_reasks
        assert "your name" in pack.forbidden_reasks

    def test_email_in_forbidden_reasks_when_in_state(self):
        state = ThreadState()
        state.personal.email = _field("cj@example.com")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "email" in pack.forbidden_reasks

    def test_phone_in_forbidden_reasks_when_in_state(self):
        state = ThreadState()
        state.personal.phone = _field("555-1234")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "phone" in pack.forbidden_reasks

    def test_contact_fields_absent_when_not_in_state(self):
        """When contact fields are not set, they must not appear in forbidden_reasks."""
        state = ThreadState()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        # name must be askable when not yet captured
        assert "name" not in pack.forbidden_reasks
        assert "email" not in pack.forbidden_reasks


# ---------------------------------------------------------------------------
# End-to-end simulation: Christopher James full scenario
# ---------------------------------------------------------------------------

class TestChristopherJamesScenario:
    """High-level integration checks simulating the Chat 5148 conversation.

    Does not call real LLM — validates that after state is updated with extracted facts,
    the context pack correctly suppresses re-asks and the planner routes appropriately.
    """

    def _state_after_intro(self) -> ThreadState:
        """State as it should look after Christopher's first message at 01:10:45."""
        state = ThreadState()
        state.personal.name = _field("Christopher James")
        state.personal.email = _field("cjames0567@yahoo.com")
        state.personal.phone = _field("337-251-3162")
        return state

    def _state_after_scratch_declared(self) -> ThreadState:
        """State as it should look after 'I started from scratch' at 01:20:22."""
        state = self._state_after_intro()
        state.project.manuscript_status = _field("not_started")
        state.project.services_discussed.append(
            ServiceInterest(
                service=_field(ServiceCategory.GHOSTWRITING),
                confidence=0.94,
            )
        )
        return state

    def test_manuscript_not_in_missing_after_scratch_declared(self):
        state = self._state_after_scratch_declared()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" not in pack.missing_facts

    def test_manuscript_in_forbidden_after_scratch_declared(self):
        state = self._state_after_scratch_declared()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" in pack.forbidden_reasks

    def test_name_not_in_missing_after_intro(self):
        state = self._state_after_intro()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        # Name must not be in missing_facts (not a slot the pack tracks, but also not askable)
        assert "name" in pack.forbidden_reasks

    def test_planner_does_not_ask_for_name_after_pricing_question(self):
        """When Christopher asks about pricing after giving name/email/phone, the planner
        must ask for email or phone — not ask for name again."""
        state = self._state_after_scratch_declared()
        pack = ContextPackBuilder().build(
            state=state,
            intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        )
        planner = ResponsePlanner()
        plan = planner.plan(
            intent=_intent(query=QueryIntentType.PRICING_QUESTION),
            state=state,
            context_pack=pack,
            lead_objective_decision=_lead_stop(),
            contact_capture_result=_contact_capture_not_ready(),
        )
        # The plan must not route back to asking for name.
        assert plan.next_question != "name_and_email_or_phone", (
            f"Planner returned 'name_and_email_or_phone' despite name being in known_facts. "
            f"Got next_question={plan.next_question!r}"
        )

    def test_response_prompt_does_not_list_manuscript_as_needed_after_scratch(self):
        """The 'What we still need' line in the generator prompt must not list
        manuscript stage after it has been declared."""
        state = self._state_after_scratch_declared()
        pack = ContextPackBuilder().build(state=state, intent=_intent())

        msg = _make_msg("I already said I started from scratch")
        prompt = _response_user_prompt(
            message=msg,
            state=state,
            intent=_intent(),
            extraction=CombinedExtraction(state_deltas=[]),
            rag_chunks=[],
            route_name="service_question",
            runtime_atoms={},
            context_pack=pack,
        )
        # "What we still need" line must not mention manuscript.
        still_need_lines = [l for l in prompt.splitlines() if "still need" in l.lower()]
        for line in still_need_lines:
            assert "manuscript" not in line.lower(), (
                f"'manuscript' appeared in 'still need' line after extraction: {line!r}"
            )

    def test_response_prompt_lists_name_in_known_after_intro(self):
        """The 'What we already know' line must show author name after it's captured."""
        state = self._state_after_intro()
        pack = ContextPackBuilder().build(state=state, intent=_intent())

        msg = _make_msg("yes")
        prompt = _response_user_prompt(
            message=msg,
            state=state,
            intent=_intent(),
            extraction=CombinedExtraction(state_deltas=[]),
            rag_chunks=[],
            route_name="service_question",
            runtime_atoms={},
            context_pack=pack,
        )
        known_lines = [l for l in prompt.splitlines() if "already know" in l.lower()]
        assert known_lines, "Prompt must have a 'What we already know' line"
        assert "christopher james" in known_lines[0].lower(), (
            f"Author name not in 'already know' line: {known_lines[0]!r}"
        )
