"""Tests for the Q&A-context extraction fix (Chat 5266 core regression).

Root cause: the extraction USER TEMPLATE placed the user message before the
assistant question, and labelled the assistant message as 'for context only —
do not extract from this'. The LLM extractor never connected the user's brief
reply to the specific field the bot had just asked about, so short answers like
'Its all in head right now.' and 'around 130,000' were not extracted.

Fix: reorder the template (assistant question FIRST, user reply SECOND) and add
an explicit Q&A extraction rule with worked examples for every major slot.
"""

from __future__ import annotations

from bookcraft.components.extraction.llm_extractor import (
    _EXTRACTION_SYSTEM,
    _EXTRACTION_USER_TEMPLATE,
    _facts_to_deltas,
)
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.extraction.state_applier import StateApplier
from bookcraft.components.context import ContextPackBuilder
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


def _render_template(user_msg: str, assistant_msg: str, known_facts: str = "  (none yet)") -> str:
    return _EXTRACTION_USER_TEMPLATE.format(
        known_facts=known_facts,
        user_message=user_msg,
        assistant_message=assistant_msg,
    )


# ---------------------------------------------------------------------------
# Template structure tests
# ---------------------------------------------------------------------------

class TestExtractionTemplateStructure:
    """The template must show the assistant's question BEFORE the user's reply
    so the LLM extractor understands the conversational Q&A context."""

    def test_assistant_message_appears_before_user_message(self):
        rendered = _render_template("user reply", "assistant question")
        pos_assistant = rendered.index("assistant question")
        pos_user = rendered.index("user reply")
        assert pos_assistant < pos_user, (
            "Assistant message must appear before user message in template "
            "so the LLM understands the user is replying to the question."
        )

    def test_template_has_qa_extraction_instruction(self):
        assert "replying to" in _EXTRACTION_USER_TEMPLATE.lower() or \
               "is replying" in _EXTRACTION_USER_TEMPLATE.lower() or \
               "in context" in _EXTRACTION_USER_TEMPLATE.lower()

    def test_template_has_examples_for_manuscript_status_qa(self):
        assert "its all in head right now" in _EXTRACTION_USER_TEMPLATE.lower() or \
               "not_started" in _EXTRACTION_USER_TEMPLATE

    def test_template_has_examples_for_word_count_qa(self):
        assert "130,000" in _EXTRACTION_USER_TEMPLATE or "word count" in _EXTRACTION_USER_TEMPLATE.lower()

    def test_rule1_now_allows_qa_implied_extractions(self):
        """Rule 1 must clarify that direct answers to questions ARE explicit statements."""
        assert "direct reply" in _EXTRACTION_SYSTEM.lower() or \
               "answering" in _EXTRACTION_SYSTEM.lower() or \
               "direct answers" in _EXTRACTION_SYSTEM.lower()


# ---------------------------------------------------------------------------
# Chat 5266 specific Q&A pairs
# ---------------------------------------------------------------------------

class TestChat5266QAPairs:
    """Simulate the exact Q&A pairs from Chat 5266 that were failing."""

    def test_template_renders_its_all_in_head_as_answer_to_question(self):
        """The rendered prompt must show the bot's question before the user's brief answer."""
        bot_q = "For a world this layered, do you have any of this written down (notes, outlines, lore docs), or is it all in your head right now?"
        user_a = "Its all in head right now."
        rendered = _render_template(user_a, bot_q)
        # Bot question must appear before user answer
        assert rendered.index(bot_q) < rendered.index(user_a)
        # The extraction instruction must be present
        assert "EXTRACTION INSTRUCTION" in rendered or "Q&A" in rendered or "replying" in rendered.lower()

    def test_around_130000_extraction_delta(self):
        """'around 130,000' → word_count 130000 at hedged confidence."""
        facts = LLMExtractedFacts(
            word_count=ExtractedValue(
                value=130000,
                confidence=0.70,
                source_quote="book one would probably be around 130,000",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert len(deltas) == 1
        assert deltas[0].path == "project.word_count"
        assert deltas[0].value == 130000
        # 0.70 < 0.85 threshold → downscaled to fill-only 0.3
        assert deltas[0].confidence == 0.3

    def test_fill_only_word_count_populates_empty_field(self):
        """A 0.3-confidence delta fills an empty word_count field."""
        applier = StateApplier()
        state = ThreadState()
        assert state.project.word_count.value is None
        state = applier.apply(state, CombinedExtraction(state_deltas=[
            StateDelta(
                path="project.word_count",
                value=130000,
                confidence=0.3,
                source=Source.AI_EXTRACTED,
                extracted_by="test",
            )
        ]))
        assert state.project.word_count.value == 130000

    def test_word_count_leaves_missing_facts_once_set(self):
        state = ThreadState()
        state.project.word_count = _field(130000)
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "word_or_page_count" not in pack.missing_facts

    def test_not_started_fills_empty_manuscript_status(self):
        applier = StateApplier()
        state = ThreadState()
        assert state.project.manuscript_status.value is None
        state = applier.apply(state, CombinedExtraction(state_deltas=[
            StateDelta(
                path="project.manuscript_status",
                value="not_started",
                confidence=0.92,
                source=Source.AI_EXTRACTED,
                extracted_by="test",
            )
        ]))
        assert state.project.manuscript_status.value == "not_started"

    def test_manuscript_stage_leaves_missing_facts_once_set(self):
        state = ThreadState()
        state.project.manuscript_status = _field("not_started")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" not in pack.missing_facts
        assert "manuscript_stage" in pack.forbidden_reasks

    def test_word_count_and_manuscript_both_absent_initially(self):
        """Baseline: before any extraction both slots appear as missing."""
        state = ThreadState()
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "word_or_page_count" in pack.missing_facts
        assert "manuscript_stage" in pack.missing_facts

    def test_after_full_extraction_neither_slot_in_missing(self):
        """After both fields are extracted, neither appears as missing."""
        state = ThreadState()
        state.project.manuscript_status = _field("not_started")
        state.project.word_count = _field(130000, confidence=0.3)
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "manuscript_stage" not in pack.missing_facts
        assert "word_or_page_count" not in pack.missing_facts

    def test_word_count_not_reasked_after_second_message(self):
        """Word count captured at turn T must still be in state at turn T+2
        (state persists across turns via the same thread)."""
        applier = StateApplier()
        state = ThreadState()

        # Turn 1: customer says "around 130,000"
        state = applier.apply(state, CombinedExtraction(state_deltas=[
            StateDelta(path="project.word_count", value=130000, confidence=0.3,
                       source=Source.AI_EXTRACTED, extracted_by="t1"),
        ]))

        # Turn 2: customer says "its a series" — no word_count info
        # (no new delta; state unchanged for word_count)

        # Turn 3: customer gives name/phone — also no word_count info
        state = applier.apply(state, CombinedExtraction(state_deltas=[
            StateDelta(path="personal.name", value="Subhan Ali", confidence=0.92,
                       source=Source.AI_EXTRACTED, extracted_by="t3"),
            StateDelta(path="personal.phone", value="8887690431", confidence=0.92,
                       source=Source.AI_EXTRACTED, extracted_by="t3"),
        ]))

        # After turn 3 the word_count must still be set
        assert state.project.word_count.value == 130000
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        assert "word_or_page_count" not in pack.missing_facts


# ---------------------------------------------------------------------------
# Template worked-example coverage
# ---------------------------------------------------------------------------

class TestTemplateExamples:
    """Every example in the template must match an actual extractable scenario."""

    def test_starting_from_scratch_example_present(self):
        assert "starting from scratch" in _EXTRACTION_USER_TEMPLATE.lower()

    def test_partially_outlined_example_present(self):
        assert "partially outlined" in _EXTRACTION_USER_TEMPLATE.lower()

    def test_name_email_example_present(self):
        template_lower = _EXTRACTION_USER_TEMPLATE.lower()
        assert "name" in template_lower and "email" in template_lower

    def test_rule1_update_references_qa_context(self):
        system_lower = _EXTRACTION_SYSTEM.lower()
        assert any(kw in system_lower for kw in ["direct reply", "direct answer", "answering"]), (
            "Rule 1 in the system prompt must clarify that Q&A replies count as explicit statements"
        )
