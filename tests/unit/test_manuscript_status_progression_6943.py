"""Regression tests for chat 6943 (Adele).

Two production defects, both traceable to the manuscript lifecycle:

1. The author said "My book is already KDP ready. I uploaded it and have a proof
   copy." — a published / print-ready book — yet the stored ``manuscript_status``
   stayed ``draft`` for the whole conversation. Two causes:
     - The deterministic detector had no KDP / proof-copy phrase, so it never fired.
     - Even a correct later extraction could not land: manuscript status advances
       monotonically, but StateApplier only replaced a fact on a *strictly greater*
       confidence, so a forward move at an equal (or the deterministic 0.86) confidence
       lost the tie and the stale ``draft`` survived.

2. The author was asking about MARKETING, but every turn pitched a free EDITORIAL
   assessment ("upload your manuscript with the attach button"). The upload pitch was
   gated only on manuscript status, never on the active service.

All contact data here is synthetic. This repo is public.
"""

from __future__ import annotations

from bookcraft.components.context import ContextPackBuilder
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.extraction.state_applier import StateApplier, should_apply_delta
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.detectors.manuscript_status_detector import (
    detect_manuscript_status,
)
from bookcraft.domain.enums import (
    ManuscriptStatus,
    QueryIntentType,
    SalesStage,
    ServiceCategory,
    Source,
    coerce_manuscript_status,
    manuscript_status_rank,
)
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ServiceInterest, ThreadState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent(service: ServiceCategory | None = None) -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=["test"],
    )


def _status_field(status: ManuscriptStatus, *, confidence: float) -> FieldMeta:
    return FieldMeta(
        value=status.value,
        confidence=confidence,
        source=Source.AI_EXTRACTED,
        extracted_by="llm_metadata_extractor.v1",
    )


def _status_delta(status: ManuscriptStatus, *, confidence: float) -> StateDelta:
    return StateDelta(
        path="project.manuscript_status",
        value=status.value,
        confidence=confidence,
        source=Source.USER_STATED,
        extracted_by="deterministic_preextractor.v1",
    )


def _state_with(status: ManuscriptStatus, service: ServiceCategory) -> ThreadState:
    state = ThreadState()
    state.project.manuscript_status = _status_field(status, confidence=0.92)
    state.project.services_discussed.append(
        ServiceInterest(service=FieldMeta(value=service, confidence=0.94), confidence=0.94)
    )
    return state


# ---------------------------------------------------------------------------
# Progression rank
# ---------------------------------------------------------------------------


def test_progression_rank_orders_the_lifecycle():
    order = [
        ManuscriptStatus.IDEA,
        ManuscriptStatus.ROUGH_NOTES,
        ManuscriptStatus.OUTLINE,
        ManuscriptStatus.IN_PROGRESS,
        ManuscriptStatus.PARTIAL_DRAFT,
        ManuscriptStatus.DRAFT,
        ManuscriptStatus.COMPLETED,
        ManuscriptStatus.EDITED,
        ManuscriptStatus.PUBLISHED,
    ]
    ranks = [manuscript_status_rank(s) for s in order]
    assert ranks == sorted(ranks)
    assert ranks == list(sorted(set(ranks))) or len(set(ranks)) == len(ranks)


def test_progression_rank_accepts_coarse_vocabulary_and_aliases():
    assert manuscript_status_rank("full_draft") == manuscript_status_rank(ManuscriptStatus.DRAFT)
    assert manuscript_status_rank("completed_draft") == manuscript_status_rank(
        ManuscriptStatus.COMPLETED
    )
    assert manuscript_status_rank("published") == manuscript_status_rank(ManuscriptStatus.PUBLISHED)
    assert manuscript_status_rank("not a status") is None


# ---------------------------------------------------------------------------
# StateApplier: forward progression wins on a confidence tie / deficit
# ---------------------------------------------------------------------------


def test_forward_progression_overrides_on_confidence_tie():
    existing = _status_field(ManuscriptStatus.DRAFT, confidence=0.92)
    incoming = _status_delta(ManuscriptStatus.PUBLISHED, confidence=0.92)  # tie
    assert should_apply_delta(existing, incoming) is True


def test_forward_progression_overrides_even_below_existing_confidence():
    """The deterministic detector stamps 0.86; it must still advance a 0.92 draft."""
    existing = _status_field(ManuscriptStatus.DRAFT, confidence=0.92)
    incoming = _status_delta(ManuscriptStatus.PUBLISHED, confidence=0.86)
    assert should_apply_delta(existing, incoming) is True


def test_low_confidence_fill_cannot_advance_a_known_status():
    existing = _status_field(ManuscriptStatus.DRAFT, confidence=0.92)
    incoming = _status_delta(ManuscriptStatus.PUBLISHED, confidence=0.3)  # fill value
    assert should_apply_delta(existing, incoming) is False


def test_backward_move_still_requires_greater_confidence():
    existing = _status_field(ManuscriptStatus.PUBLISHED, confidence=0.92)
    incoming = _status_delta(ManuscriptStatus.DRAFT, confidence=0.92)  # tie, backward
    assert should_apply_delta(existing, incoming) is False


def test_applier_advances_draft_to_published_end_to_end():
    state = ThreadState()
    state.project.manuscript_status = _status_field(ManuscriptStatus.DRAFT, confidence=0.92)
    state = StateApplier().apply(
        state,
        CombinedExtraction(state_deltas=[_status_delta(ManuscriptStatus.PUBLISHED, confidence=0.86)]),
    )
    assert state.project.manuscript_status.value == ManuscriptStatus.PUBLISHED.value


# ---------------------------------------------------------------------------
# Deterministic detector recognises KDP / print-ready signals
# ---------------------------------------------------------------------------


def test_kdp_ready_and_proof_copy_detected_as_published():
    status = detect_manuscript_status(
        "My book is already KDP ready. I uploaded it and have a proof copy."
    )
    assert status == ManuscriptStatus.PUBLISHED


def test_kdp_ready_survives_a_cooccurring_publishing_goal_phrase():
    status = detect_manuscript_status(
        "I want to get it published — it's already KDP-ready with a proof copy."
    )
    assert status == ManuscriptStatus.PUBLISHED


def test_plain_publishing_goal_is_not_a_published_status():
    assert detect_manuscript_status("I want to publish my book") is None


# ---------------------------------------------------------------------------
# Assessment / upload pitch is gated on the active service
# ---------------------------------------------------------------------------


def test_marketing_focus_suppresses_the_editorial_assessment_pitch():
    state = _state_with(ManuscriptStatus.DRAFT, ServiceCategory.MARKETING_PROMOTION)
    pack = ContextPackBuilder().build(state=state, intent=_intent())
    assert pack.manuscript_upload_eligible is False
    hint = (pack.response_hint or "").casefold()
    assert "editorial assessment" not in hint
    assert "upload your manuscript" not in hint


def test_publishing_focus_keeps_the_assessment_pitch_for_a_draft():
    state = _state_with(ManuscriptStatus.DRAFT, ServiceCategory.PUBLISHING_DISTRIBUTION)
    pack = ContextPackBuilder().build(state=state, intent=_intent())
    assert pack.manuscript_upload_eligible is True


def test_editing_focus_keeps_the_assessment_pitch():
    state = _state_with(ManuscriptStatus.DRAFT, ServiceCategory.EDITING_PROOFREADING)
    pack = ContextPackBuilder().build(state=state, intent=_intent())
    assert pack.manuscript_upload_eligible is True


def test_published_book_is_never_offered_an_editorial_assessment():
    """A finished / distributed book has nothing to editorially assess."""
    state = _state_with(ManuscriptStatus.PUBLISHED, ServiceCategory.PUBLISHING_DISTRIBUTION)
    pack = ContextPackBuilder().build(state=state, intent=_intent())
    assert pack.manuscript_upload_eligible is False


def test_coerce_accepts_completed_and_published_from_llm_vocabulary():
    """The extractor may now emit these canonical tokens directly."""
    assert coerce_manuscript_status("completed") == ManuscriptStatus.COMPLETED
    assert coerce_manuscript_status("published") == ManuscriptStatus.PUBLISHED
