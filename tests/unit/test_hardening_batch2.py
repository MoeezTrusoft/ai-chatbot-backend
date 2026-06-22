"""Batch 2 hardening unit tests.

Fake PII only: John Smith / john@example.com / 5551234567
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Step 1: Deterministic consultation extraction
# ---------------------------------------------------------------------------


def test_free_consultation_phrase_triggers():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("I need the free consultation please")
    assert result["requested"] is True


def test_book_a_call_triggers():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Can I book a call with your team?")
    assert result["requested"] is True


def test_talk_to_someone_triggers():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Can I talk to someone tomorrow?")
    assert result["requested"] is True


def test_call_me_tomorrow_at_4pm_extracts_datetime():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Call me tomorrow at 4pm")
    assert result["requested"] is True
    assert result["requested_date_text"] is not None
    assert "tomorrow" in str(result["requested_date_text"]).lower()
    assert result["requested_time_text"] is not None
    assert "4pm" in str(result["requested_time_text"]).lower()
    # Combined datetime phrase should be preserved
    assert result["requested_datetime_text"] is not None
    assert "tomorrow" in str(result["requested_datetime_text"]).lower()


def test_friday_afternoon_extracts():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Friday afternoon works for a call")
    assert result["requested"] is True
    assert result["requested_date_text"] is not None
    assert "friday" in str(result["requested_date_text"]).lower()


def test_timezone_extracted():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Tomorrow at 4pm PST works for the consultation")
    assert result["timezone_text"] is not None
    assert "PST" in str(result["timezone_text"])


def test_non_consultation_does_not_trigger():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("I need help with my book formatting")
    assert result == {}


def test_zoom_channel_extracted():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Can we schedule a call tomorrow via Zoom?")
    assert result["requested"] is True
    assert result["channel_preference"] == "zoom"


def test_timezone_unknown_set_when_relative_time_no_tz():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Let's schedule a call tomorrow afternoon")
    assert result["timezone_unknown"] is True


def test_timezone_unknown_false_when_tz_known():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Let's schedule a call tomorrow afternoon", known_timezone="EST")
    assert result["timezone_unknown"] is False


# ---------------------------------------------------------------------------
# Step 2: Service keyword contamination
# ---------------------------------------------------------------------------


def test_help_writing_story_does_not_trigger_cover_design():
    """'only have an idea and need help writing the story' must NOT match cover design."""
    from bookcraft.components.preprocessor.processor import SERVICE_KEYWORDS
    from bookcraft.domain.enums import ServiceCategory

    cover_kws = SERVICE_KEYWORDS[ServiceCategory.COVER_DESIGN_ILLUSTRATION]
    _writing_terms = [
        "help writing",
        "only have an idea",
        "story writing",
        "write the story",
        "help me write",
    ]
    contaminated = [kw for kw in cover_kws if any(term in kw.lower() for term in _writing_terms)]
    assert not contaminated, f"Cover design still has writing keywords: {contaminated}"


def test_book_cover_triggers_cover_design():
    from bookcraft.components.preprocessor.processor import SERVICE_KEYWORDS
    from bookcraft.domain.enums import ServiceCategory

    cover_kws = SERVICE_KEYWORDS[ServiceCategory.COVER_DESIGN_ILLUSTRATION]
    assert "book cover" in cover_kws


# ---------------------------------------------------------------------------
# Step 3: Standalone "cover" removed from service keywords
# ---------------------------------------------------------------------------


def test_standalone_cover_not_in_keywords():
    from bookcraft.components.preprocessor.processor import SERVICE_KEYWORDS
    from bookcraft.domain.enums import ServiceCategory

    cover_kws = SERVICE_KEYWORDS[ServiceCategory.COVER_DESIGN_ILLUSTRATION]
    assert "cover" not in cover_kws, "Standalone 'cover' must not be a service keyword — too broad"


def test_cover_design_keyword_present():
    from bookcraft.components.preprocessor.processor import SERVICE_KEYWORDS
    from bookcraft.domain.enums import ServiceCategory

    cover_kws = SERVICE_KEYWORDS[ServiceCategory.COVER_DESIGN_ILLUSTRATION]
    assert "cover design" in cover_kws


# ---------------------------------------------------------------------------
# Step 4: Correction detection
# ---------------------------------------------------------------------------


def test_actually_is_correction():
    from bookcraft.components.extraction.extractor import is_correction_turn

    assert is_correction_turn("Actually it's fantasy, not memoir.")


def test_i_meant_is_correction():
    from bookcraft.components.extraction.extractor import is_correction_turn

    assert is_correction_turn("Wait, I meant sci-fi.")


def test_now_its_is_correction():
    from bookcraft.components.extraction.extractor import is_correction_turn

    assert is_correction_turn("Now it's definitely thriller.")


def test_normal_message_not_correction():
    from bookcraft.components.extraction.extractor import is_correction_turn

    assert not is_correction_turn("I need help publishing my book.")


# ---------------------------------------------------------------------------
# Step 5: State applier — correction overrides equal confidence
# ---------------------------------------------------------------------------


def test_correction_source_overwrites_equal_confidence():
    from bookcraft.components.extraction.schemas import StateDelta
    from bookcraft.components.extraction.state_applier import should_apply_delta
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    existing = FieldMeta[str](value="memoir", confidence=0.9, source=Source.USER_STATED)
    incoming = StateDelta(
        path="project.genre",
        value="fantasy",
        confidence=0.9,  # same confidence
        source=Source.USER_CORRECTED,  # but CORRECTED
        extracted_by="test",
    )
    assert should_apply_delta(existing, incoming) is True


def test_non_correction_equal_confidence_does_not_overwrite():
    from bookcraft.components.extraction.schemas import StateDelta
    from bookcraft.components.extraction.state_applier import should_apply_delta
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    existing = FieldMeta[str](value="memoir", confidence=0.9, source=Source.USER_STATED)
    incoming = StateDelta(
        path="project.genre",
        value="fantasy",
        confidence=0.9,
        source=Source.USER_STATED,  # not corrected
        extracted_by="test",
    )
    assert should_apply_delta(existing, incoming) is False


# ---------------------------------------------------------------------------
# Step 6: State applier safe path handling
# ---------------------------------------------------------------------------


def test_invalid_state_path_does_not_crash():
    from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
    from bookcraft.components.extraction.state_applier import StateApplier
    from bookcraft.domain.enums import Source
    from bookcraft.domain.state import ThreadState

    extractor = StateApplier()
    state = ThreadState()
    bad_delta = StateDelta(
        path="nonexistent.field",
        value="x",
        confidence=0.9,
        source=Source.USER_STATED,
        extracted_by="test",
    )
    extraction = CombinedExtraction()
    extraction.state_deltas.append(bad_delta)

    rejected = []
    result = extractor.apply(state, extraction, rejected_paths=rejected)
    # Must not raise; rejected paths must be recorded.
    assert result is not None
    assert "nonexistent.field" in rejected


def test_valid_state_path_still_applies():
    from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
    from bookcraft.components.extraction.state_applier import StateApplier
    from bookcraft.domain.enums import Source
    from bookcraft.domain.state import ThreadState

    extractor = StateApplier()
    state = ThreadState()
    good_delta = StateDelta(
        path="project.genre",
        value="sci-fi",
        confidence=0.9,
        source=Source.USER_STATED,
        extracted_by="test",
    )
    extraction = CombinedExtraction()
    extraction.state_deltas.append(good_delta)

    result = extractor.apply(state, extraction)
    assert result.project.genre.value == "sci-fi"


# ---------------------------------------------------------------------------
# Step 10: Quote attempt count increments
# ---------------------------------------------------------------------------


def test_pricing_attempt_count_increments():
    """MISSING_INFO plan must increment quote_attempt_count in state."""
    from bookcraft.components.actions.planner import SalesActionPlanner
    from bookcraft.components.actions.schemas import ActionStatus
    from bookcraft.components.extraction.schemas import CombinedExtraction
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.preprocessor.schemas import ProcessedMessage
    from bookcraft.domain.enums import QueryIntentType, SalesStage
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    assert state.sales_actions.pricing.quote_attempt_count == 0

    processed = ProcessedMessage(
        raw="how much does ghostwriting cost?",
        normalized="how much does ghostwriting cost?",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[0.0],
        language="en",
        char_count=32,
    )
    intent = IntentVote(
        query_primary=QueryIntentType.PRICING_QUESTION,
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
    assert plan.status == ActionStatus.MISSING_INFO
    # State must have been incremented.
    assert state.sales_actions.pricing.quote_attempt_count == 1


# ---------------------------------------------------------------------------
# Step 11: Deadline not added when known
# ---------------------------------------------------------------------------


def test_deadline_not_in_missing_when_known():
    """If deadline is already in project slots, it should not be in missing."""
    from datetime import UTC, datetime

    from bookcraft.components.actions.planner import SalesActionPlanner
    from bookcraft.components.actions.slot_resolver import project_slots
    from bookcraft.components.extraction.schemas import CombinedExtraction
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.preprocessor.schemas import ProcessedMessage
    from bookcraft.domain.enums import QueryIntentType, SalesStage, Source
    from bookcraft.domain.meta import FieldMeta
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    state.project.target_completion_date = FieldMeta[datetime](
        value=datetime(2026, 12, 1, tzinfo=UTC),
        confidence=0.95,
        source=Source.USER_STATED,
        extracted_by="test",
    )
    processed = ProcessedMessage(
        raw="how much will ghostwriting cost?",
        normalized="how much will ghostwriting cost?",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[0.0],
        language="en",
        char_count=32,
    )
    # Simulate that project_slots includes deadline
    ext = CombinedExtraction()
    intent = IntentVote(
        query_primary=QueryIntentType.PRICING_QUESTION,
        funnel_stage=SalesStage.SCOPING,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )
    project = project_slots(state=state, extraction=ext, processed=processed)
    # Should have deadline from state
    # (The planner uses project_slots output — if deadline is there, it won't be missing)
    planner = SalesActionPlanner()
    plan = planner.plan(processed=processed, state=state, intent=intent, extraction=ext)
    # Deadline should not be in missing slots if it's in project
    if "deadline" in project or "target_launch_window" in project:
        assert "deadline" not in plan.missing_slots


# ---------------------------------------------------------------------------
# Step 13/14: Consultation time extraction specificity
# ---------------------------------------------------------------------------


def test_tomorrow_at_4pm_is_preserved():
    """'tomorrow at 4pm' should yield full datetime phrase, not just 'tomorrow'."""
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Yes, tomorrow at 4pm is fine for the consultation")
    assert result["requested"] is True
    dt = result["requested_datetime_text"]
    assert dt is not None
    assert "tomorrow" in str(dt).lower()
    assert "4pm" in str(dt).lower() or "4 pm" in str(dt).lower()


def test_full_message_not_used_as_time():
    """requested_time_text must not contain non-time words from the sentence."""
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation(
        "Yes, tomorrow at 4pm is fine, and use my email from above for the consultation"
    )
    dt = result.get("requested_datetime_text") or ""
    # Must not contain "my email from above" style sentence fragments
    assert "email" not in dt.lower()
    assert "above" not in dt.lower()
    assert "fine" not in dt.lower()


# ---------------------------------------------------------------------------
# Step 15: Timezone unknown flag
# ---------------------------------------------------------------------------


def test_timezone_unknown_true_when_relative_no_tz():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Let's have a consultation call tomorrow morning")
    assert result["timezone_unknown"] is True


def test_timezone_unknown_false_when_tz_in_message():
    from bookcraft.components.extraction.extractor import extract_consultation

    result = extract_consultation("Let's schedule for tomorrow 10am EST")
    assert result["timezone_unknown"] is False
    assert result["timezone_text"] is not None


# ---------------------------------------------------------------------------
# Step 16: Consultation handoff guard in state
# ---------------------------------------------------------------------------


def test_consultation_handoff_created_flag_default_false():
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    assert state.consultation_handoff_created is False


def test_consultation_handoff_created_flag_settable():
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    state.consultation_handoff_created = True
    assert state.consultation_handoff_created is True


# ---------------------------------------------------------------------------
# Step 17: preferred_call_time slot name maps to human question
# ---------------------------------------------------------------------------


def test_preferred_call_time_maps_to_human_question():
    from bookcraft.components.response.quality_gate import _fact_key_to_question

    q = _fact_key_to_question("preferred_call_time")
    # Must not contain raw slot name
    assert "preferred_call_time" not in q
    assert "?" in q  # must be a proper question


def test_preferred_call_timezone_maps_to_human_question():
    from bookcraft.components.response.quality_gate import _fact_key_to_question

    q = _fact_key_to_question("preferred_call_timezone")
    assert "preferred_call_timezone" not in q
    assert "timezone" in q.lower() or "time" in q.lower()


def test_unknown_slot_key_gets_humanized():
    """Unknown slot keys must not appear literally in output."""
    from bookcraft.components.response.quality_gate import _fact_key_to_question

    q = _fact_key_to_question("some_internal_slot_name")
    # Should not repeat the raw underscore form literally
    assert "some_internal_slot_name" not in q


# ---------------------------------------------------------------------------
# Step 18: No hardcoded "memoir" in NDA response
# ---------------------------------------------------------------------------


def test_nda_scifi_response_does_not_say_memoir():
    """NDA template should use actual genre, not hardcode 'memoir'."""
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.preprocessor.schemas import ProcessedMessage
    from bookcraft.components.response.generator import _humanized_template_response
    from bookcraft.domain.enums import QueryIntentType, SalesStage, Source
    from bookcraft.domain.meta import FieldMeta
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    state.project.genre = FieldMeta[str](
        value="sci-fi", confidence=0.95, source=Source.USER_STATED, extracted_by="test"
    )
    intent = IntentVote(
        query_primary=QueryIntentType.NDA_REQUEST,
        funnel_stage=SalesStage.NDA_REQUESTED,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )
    processed = ProcessedMessage(
        raw="I need an NDA",
        normalized="i need an nda",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[0.0],
        language="en",
        char_count=13,
    )
    response = _humanized_template_response(
        intent=intent,
        state=state,
        message=processed,
        runtime_atoms={},
        rag_chunks=[],
        route_name="default",
    )
    assert "memoir" not in response.lower()
    assert "sci-fi" in response.lower() or "your project" in response.lower()


def test_nda_unknown_genre_uses_generic_project_reference():
    """When genre unknown, NDA response should say 'your project' not 'memoir'."""
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.preprocessor.schemas import ProcessedMessage
    from bookcraft.components.response.generator import _humanized_template_response
    from bookcraft.domain.enums import QueryIntentType, SalesStage
    from bookcraft.domain.state import ThreadState

    state = ThreadState()  # no genre set
    intent = IntentVote(
        query_primary=QueryIntentType.NDA_REQUEST,
        funnel_stage=SalesStage.NDA_REQUESTED,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )
    processed = ProcessedMessage(
        raw="NDA please",
        normalized="nda please",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[0.0],
        language="en",
        char_count=10,
    )
    response = _humanized_template_response(
        intent=intent,
        state=state,
        message=processed,
        runtime_atoms={},
        rag_chunks=[],
        route_name="default",
    )
    assert "memoir" not in response.lower()
    assert "your project" in response.lower()


# ---------------------------------------------------------------------------
# Step 19: Ready-to-buy template does not claim unshared facts
# ---------------------------------------------------------------------------


def test_ready_to_buy_no_stage_no_genre_does_not_claim_them():
    """READY_TO_BUY template must not say 'you shared the stage and category' when they weren't."""
    from bookcraft.components.context.schemas import ContextPack
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.preprocessor.schemas import ProcessedMessage
    from bookcraft.components.response.generator import _humanized_template_response
    from bookcraft.domain.enums import QueryIntentType, SalesStage
    from bookcraft.domain.state import ThreadState

    state = ThreadState()  # nothing known
    pack = ContextPack(known_facts=[])  # no known facts
    intent = IntentVote(
        query_primary=QueryIntentType.READY_TO_BUY,
        funnel_stage=SalesStage.SCOPING,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )
    processed = ProcessedMessage(
        raw="I'm ready to start",
        normalized="i'm ready to start",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[0.0],
        language="en",
        char_count=18,
    )
    response = _humanized_template_response(
        intent=intent,
        state=state,
        message=processed,
        runtime_atoms={},
        rag_chunks=[],
        route_name="default",
        context_pack=pack,
    )
    # Must not claim the user shared stage/category when pack.known_facts is empty.
    assert "you've shared the manuscript stage and category" not in response
    assert "you've shared the manuscript stage" not in response
    assert "you've shared the category" not in response


# ── Chat 6211: raw RAG/FAQ document text leaked into the customer reply ────────
def _faq_chunk():
    """The verbatim knowledge-base prose that bled into chat 6211's reply."""
    from bookcraft.components.rag.schemas import RetrievedChunk

    return RetrievedChunk(
        chunk_id="c1",
        content=(
            "embedded Arabic quotations, for example). Will you advise on the best "
            "trim size for my book? Yes. Trim size matters for genre conventions "
            "(mass-market paperbacks are 4.25×6.87; trade paperbacks are typically "
            "5.5×8.5 or 6×9; literary fiction often uses 5.25×8) and for cost "
            "efficiency at print-on-demand."
        ),
        score=0.9,
        section="formatting",
        source_id="formatting_faq",
        title="Formatting FAQ",
        checksum="x",
        citation="Formatting FAQ",
    )


def _processed(raw: str):
    from bookcraft.components.preprocessor.schemas import ProcessedMessage

    return ProcessedMessage(
        raw=raw, normalized=raw.lower(), tokens=[], negation_spans=[], hedge_spans=[],
        counterfactual_spans=[], deterministic_atoms={}, embedding=[0.0],
        language="en", char_count=len(raw),
    )


def test_greeting_template_never_splices_raw_rag_text():
    """BUG chat 6211: the greeting fallback spliced a raw FAQ chunk into the reply.
    The deterministic template must never contain verbatim retrieved-document text."""
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.response.generator import _humanized_template_response
    from bookcraft.components.response.planner import ResponsePlan
    from bookcraft.domain.enums import QueryIntentType, SalesStage
    from bookcraft.domain.state import ThreadState

    intent = IntentVote(
        query_primary=QueryIntentType.GREETING, funnel_stage=SalesStage.NEW,
        needs_clarification=False, confidence=0.9, rationale="t", evidence=[],
    )
    plan = ResponsePlan(primary_goal="greeting_welcome")
    response = _humanized_template_response(
        intent=intent, state=ThreadState(), message=_processed("hi"),
        runtime_atoms={}, rag_chunks=[_faq_chunk()], route_name="default",
        response_plan=plan,
    )
    assert "trim size" not in response.lower()
    assert "4.25×6.87" not in response
    assert "Will you advise" not in response
    assert response.startswith("Welcome to BookCraft!")


def test_manuscript_update_template_never_splices_raw_rag_text():
    """The 'That's great progress' branch also previously spliced the raw chunk."""
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.components.response.generator import _humanized_template_response
    from bookcraft.domain.enums import QueryIntentType, SalesStage
    from bookcraft.domain.state import ThreadState

    intent = IntentVote(
        query_primary=QueryIntentType.MANUSCRIPT_STATUS_UPDATE, funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False, confidence=0.9, rationale="t", evidence=[],
    )
    response = _humanized_template_response(
        intent=intent, state=ThreadState(), message=_processed("finished my draft"),
        runtime_atoms={}, rag_chunks=[_faq_chunk()], route_name="default",
    )
    assert "trim size" not in response.lower()
    assert "4.25" not in response


def test_verbatim_bleed_caught_by_ngram_not_brittle_regex():
    """Verbatim document bleed is caught by the document-agnostic n-gram overlap
    detector (quality gate Check 24), NOT by brittle phrase regexes. Crucially, a
    PARAPHRASED reply that conversationally mentions trim sizes must NOT be rejected
    (a real live reply: "6x9 is the most common ... 5.5x8.5 is a solid alternative")."""
    from types import SimpleNamespace

    from bookcraft.components.response.generator import _contains_doc_artifacts
    from bookcraft.components.response.quality_gate import _verbatim_rag_overlap

    chunk = SimpleNamespace(
        content=(
            "Will you advise on the best trim size for my book? Yes. Trim size matters "
            "for genre conventions (mass-market paperbacks are 4.25x6.87)."
        )
    )
    # Verbatim copy → n-gram detector flags it.
    leaked = "Sure. Will you advise on the best trim size for my book? Yes. Trim size matters."
    assert _verbatim_rag_overlap(leaked, [chunk]) is not None

    # Good paraphrased advice → NOT flagged by the n-gram detector...
    good = (
        "For dark fantasy, 6x9 is the most common trade paperback trim. "
        "5.5x8.5 is a solid alternative for a more mass-market feel. What's your page count?"
    )
    assert _verbatim_rag_overlap(good, [chunk]) is None
    # ...and NOT over-rejected by the coarse doc-artifact guard either.
    assert not _contains_doc_artifacts(good)
