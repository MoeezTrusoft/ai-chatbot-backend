"""Regression for chat 6992 — a low-confidence extraction must not populate a
high-impact semantic field on first write, and a publishing GOAL is not the
published STATE.

Bug: customer said only "Yes need to publish it" (a future goal). The LLM extractor
mis-read the token "publish" as manuscript_status="published" at its own sub-0.85
confidence, which _delta_confidence floors to 0.3. Because the field was empty,
should_apply_delta's first-write path wrote it unconditionally (no absolute floor),
and the response layer then stated "since it's already published…".

Two independent guards are asserted here:
  1. state_applier: a 0.3 first-write onto empty manuscript_status is rejected.
  2. the deterministic detector already treats "need to publish it" as a goal (None);
     that behaviour is pinned so the two layers agree.
"""

from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.extraction.state_applier import StateApplier, should_apply_delta
from bookcraft.components.preprocessor.detectors.manuscript_status_detector import (
    detect_manuscript_status,
)
from bookcraft.domain.enums import ManuscriptStatus, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


def _status_delta(value: ManuscriptStatus, confidence: float, source=Source.AI_EXTRACTED):
    return StateDelta(
        path="project.manuscript_status",
        value=value.value,
        confidence=confidence,
        source=source,
        extracted_by="llm_metadata_extractor.v1",
        raw_excerpt="need to publish it",
    )


# ---------------------------------------------------------------------------
# state_applier first-write floor
# ---------------------------------------------------------------------------


def test_low_confidence_fill_rejected_on_empty_manuscript_status():
    """The exact chat-6992 delta: 0.3 published onto an empty field must be blocked."""
    empty = FieldMeta[ManuscriptStatus]()
    incoming = _status_delta(ManuscriptStatus.PUBLISHED, confidence=0.3)
    assert should_apply_delta(empty, incoming) is False
    assert should_apply_delta(None, incoming) is False


def test_clear_extraction_still_fills_empty_manuscript_status():
    """A genuine 0.92 extraction still populates an empty field."""
    empty = FieldMeta[ManuscriptStatus]()
    incoming = _status_delta(ManuscriptStatus.PUBLISHED, confidence=0.92)
    assert should_apply_delta(empty, incoming) is True


def test_deterministic_detector_confidence_clears_floor():
    """The deterministic detector stamps 0.86 — above the 0.60 first-write floor."""
    empty = FieldMeta[ManuscriptStatus]()
    incoming = _status_delta(ManuscriptStatus.DRAFT, confidence=0.86)
    assert should_apply_delta(empty, incoming) is True


def test_user_correction_bypasses_first_write_floor():
    """An explicit user correction wins even below the floor and even on empty."""
    empty = FieldMeta[ManuscriptStatus]()
    incoming = _status_delta(
        ManuscriptStatus.PUBLISHED, confidence=0.3, source=Source.USER_CORRECTED
    )
    assert should_apply_delta(empty, incoming) is True


def test_other_fields_keep_low_confidence_fill_behaviour():
    """The floor is scoped: word_count still accepts a 0.3 fill onto an empty field."""
    empty = FieldMeta[int]()
    incoming = StateDelta(
        path="project.word_count",
        value=130000,
        confidence=0.3,
        source=Source.AI_EXTRACTED,
        extracted_by="llm_metadata_extractor.v1",
        raw_excerpt="around 130,000",
    )
    assert should_apply_delta(empty, incoming) is True


def test_applier_end_to_end_ignores_low_conf_published():
    """Full apply(): the 0.3 published delta leaves manuscript_status empty."""
    state = ThreadState()
    delta = _status_delta(ManuscriptStatus.PUBLISHED, confidence=0.3)
    state = StateApplier().apply(state, CombinedExtraction(state_deltas=[delta]))
    assert state.project.manuscript_status.value is None


# ---------------------------------------------------------------------------
# publishing goal vs published state (deterministic layer agrees)
# ---------------------------------------------------------------------------


def test_need_to_publish_is_a_goal_not_published():
    assert detect_manuscript_status("Yes need to publish it") is None


def test_want_to_publish_is_a_goal_not_published():
    assert detect_manuscript_status("I want to get it published") is None
