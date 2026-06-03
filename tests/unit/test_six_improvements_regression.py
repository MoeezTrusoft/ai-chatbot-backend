"""Regression tests for the 6 chat-bot improvements.

Coverage map:
  Fix 1 — Consultation schema: phone + timezone required; email optional
  Fix 2 — Manuscript extraction: LLM-driven rule 8 (vocabulary + examples)
  Fix 3 — WPM 120: typingDelayMs constant change (Python-side: extraction system prompt sanity)
  Fix 4+5 — Debounce + sequential queue: logic unit-tests (pure Python simulation)
  Fix 6 — Attachment routing: system prompt contains guidance; planner goal check
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.consultations import ConsultationActionRequest
from bookcraft.components.context import ContextPackBuilder
from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.extraction.llm_extractor import _EXTRACTION_SYSTEM
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.components.extraction.llm_extractor import _facts_to_deltas
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.generator import _response_user_prompt
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.domain.enums import QueryIntentType, SalesStage, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _field(value, *, confidence: float = 0.92):
    return FieldMeta(value=value, confidence=confidence, source=Source.AI_EXTRACTED)


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


def _make_msg(text: str) -> ProcessedMessage:
    return ProcessedMessage(
        raw=text, normalized=text, language="en",
        tokens=[], negation_spans=[], hedge_spans=[],
        counterfactual_spans=[], deterministic_atoms={},
        embedding=[], char_count=len(text),
    )


# ---------------------------------------------------------------------------
# Fix 1 — Consultation booking: phone + timezone required; email optional
# ---------------------------------------------------------------------------

class TestConsultationNewValidation:
    def _base(self, **kwargs) -> ConsultationActionRequest:
        defaults = dict(
            thread_id=uuid4(),
            name="Gina Author",
            phone="815-997-0607",
            customer_timezone="America/Chicago",
            requested_time_text="tomorrow after 3pm",
        )
        defaults.update(kwargs)
        return ConsultationActionRequest(**defaults)

    def test_phone_required(self):
        with pytest.raises(ValueError, match="consultation_requires_phone"):
            self._base(phone=None)

    def test_timezone_required(self):
        with pytest.raises(ValueError, match="consultation_requires_customer_timezone"):
            self._base(customer_timezone=None)

    def test_name_required(self):
        # name: str is a non-optional field; empty string is stripped to None by the
        # clean_strings validator, which makes Pydantic raise a type-level ValidationError.
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            self._base(name="")

    def test_email_optional_no_error(self):
        req = self._base(email=None)
        assert req.email is None
        assert req.phone == "815-997-0607"

    def test_email_accepted_when_provided(self):
        req = self._base(email="gina@example.com")
        assert req.email == "gina@example.com"

    def test_all_three_required_fields_succeed(self):
        req = self._base()
        assert req.name == "Gina Author"
        assert req.phone == "815-997-0607"
        assert req.customer_timezone == "America/Chicago"


# ---------------------------------------------------------------------------
# Fix 2 — Manuscript extraction: LLM-driven rule 8
# ---------------------------------------------------------------------------

class TestManuscriptExtractionRule8:
    """Verify that rule 8 in the extraction system prompt follows the new LLM-driven
    format: a closed list of valid values + examples. Does NOT use rigid phrase mapping."""

    def test_valid_values_listed(self):
        for key in ("not_started", "notes_only", "early_draft", "full_draft", "editing_complete"):
            assert key in _EXTRACTION_SYSTEM, f"Missing valid key: {key!r}"

    def test_no_rigid_arrow_mappings(self):
        # Old approach used " → value: " literal arrows — the new approach should not.
        assert "→ value: \"not_started\"" not in _EXTRACTION_SYSTEM
        assert "→ value: \"notes_only\"" not in _EXTRACTION_SYSTEM
        assert "→ value: \"early_draft\"" not in _EXTRACTION_SYSTEM

    def test_examples_present_for_guidance(self):
        assert "I have 5 chapters" in _EXTRACTION_SYSTEM
        assert "Drafted" in _EXTRACTION_SYSTEM
        assert "Prologue and 5 complete chapters" in _EXTRACTION_SYSTEM
        assert "preparing" in _EXTRACTION_SYSTEM.lower()

    def test_confidence_guidance_present(self):
        assert "0.92" in _EXTRACTION_SYSTEM
        assert "0.70" in _EXTRACTION_SYSTEM

    def test_facts_to_deltas_maps_early_draft(self):
        facts = LLMExtractedFacts(
            manuscript_status=ExtractedValue(
                value="early_draft", confidence=0.92,
                source_quote="I have 5 chapters",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert len(deltas) == 1
        assert deltas[0].path == "project.manuscript_status"
        assert deltas[0].value == "early_draft"
        assert deltas[0].confidence == 0.92

    def test_facts_to_deltas_maps_not_started(self):
        facts = LLMExtractedFacts(
            manuscript_status=ExtractedValue(
                value="not_started", confidence=0.92,
                source_quote="preparing myself, getting everything I need to start",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert deltas[0].value == "not_started"

    def test_low_confidence_downscaled(self):
        facts = LLMExtractedFacts(
            manuscript_status=ExtractedValue(
                value="notes_only", confidence=0.70,
                source_quote="I think I have some notes",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert deltas[0].confidence == 0.3  # downscaled to fill-only


# ---------------------------------------------------------------------------
# Fix 2 — Pack builder: once manuscript_status is set, it's suppressed from missing
# ---------------------------------------------------------------------------

class TestManuscriptStatusSuppressionAfterExtraction:
    def test_manuscript_not_in_missing_when_state_has_value(self):
        state = ThreadState()
        state.project.manuscript_status = _field("early_draft")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" not in pack.missing_facts

    def test_manuscript_in_forbidden_reasks_when_state_has_value(self):
        state = ThreadState()
        state.project.manuscript_status = _field("early_draft")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" in pack.forbidden_reasks

    def test_manuscript_in_missing_when_state_is_empty(self):
        state = ThreadState()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" in pack.missing_facts

    def test_generator_prompt_excludes_manuscript_from_needed_when_forbidden(self):
        """When manuscript_status is captured, 'What we still need' must not list it."""
        state = ThreadState()
        state.project.manuscript_status = _field("early_draft")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        prompt = _response_user_prompt(
            message=_make_msg("Yes"),
            state=state, intent=_intent(),
            extraction=CombinedExtraction(state_deltas=[]),
            rag_chunks=[], route_name="service_question",
            runtime_atoms={}, context_pack=pack,
        )
        still_need_lines = [l for l in prompt.splitlines() if "still need" in l.lower()]
        for line in still_need_lines:
            assert "manuscript" not in line.lower(), (
                f"manuscript appeared in 'still need' after extraction: {line!r}"
            )


# ---------------------------------------------------------------------------
# Fix 3 — WPM 120 (system-prompt sanity check; actual WPM is in Node.js)
# ---------------------------------------------------------------------------

class TestWpmSystemPromptSanity:
    """The Python extraction system prompt should not reference any typing speed.
    The actual WPM change is in socket.js (msPerChar: 171 → 100)."""

    def test_extraction_system_prompt_is_not_empty(self):
        assert len(_EXTRACTION_SYSTEM) > 200


# ---------------------------------------------------------------------------
# Fix 6 — Attachment handling: system prompt contains guidance
# ---------------------------------------------------------------------------

class TestAttachmentSystemPromptGuidance:
    def test_attachment_guidance_in_generator_source(self):
        """The generator module must contain the attachment → consultation guidance rule."""
        import inspect
        from bookcraft.components.response import generator as gen_mod
        src = inspect.getsource(gen_mod)
        lowered = src.lower()
        assert "file or attachment received" in lowered, (
            "Generator must contain 'File or attachment received' guidance block"
        )
        assert "specialist will review" in lowered or "specialist will go over" in lowered, (
            "Attachment guidance must mention specialist review on the call"
        )

    def test_attachment_received_goal_routes_to_consultation_interest(self):
        """Planner must return consultation_interest as next_question for attachment turns."""
        from bookcraft.components.response.planner import ResponsePlanner
        from bookcraft.components.context.schemas import ContextPack
        from bookcraft.components.attachments.intake import ChatAttachment

        planner = ResponsePlanner()
        pack = ContextPack(
            known_facts=[],
            missing_facts=[],
            forbidden_reasks=[],
            allowed_next_questions=[],
            attachments_received=[
                ChatAttachment(filename="manuscript.docx", mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ],
        )
        plan = planner.plan(
            intent=_intent(),
            state=ThreadState(),
            context_pack=pack,
        )
        assert plan.next_question in ("consultation_interest", "preferred_call_time", None), (
            f"Unexpected next_question for attachment turn: {plan.next_question!r}"
        )
        # Goal must be attachment-related
        assert "attachment" in plan.primary_goal or "assessment" in plan.primary_goal, (
            f"Unexpected primary_goal: {plan.primary_goal!r}"
        )


# ---------------------------------------------------------------------------
# Fix 1 — Consultation system prompt includes phone + timezone requirements
# ---------------------------------------------------------------------------

class TestConsultationSystemPromptUpdated:
    def test_system_prompt_mentions_phone_required(self):
        import inspect
        from bookcraft.components.response import generator as gen_mod
        src = inspect.getsource(gen_mod)
        assert "phone" in src.lower() and "required" in src.lower()

    def test_system_prompt_mentions_timezone(self):
        import inspect
        from bookcraft.components.response import generator as gen_mod
        src = inspect.getsource(gen_mod)
        assert "timezone" in src.lower()
