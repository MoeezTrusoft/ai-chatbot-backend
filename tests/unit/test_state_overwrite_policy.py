from __future__ import annotations

from bookcraft.components.extraction import StateApplier
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.domain.enums import Source
from bookcraft.domain.state import ThreadState


def test_equal_confidence_genre_delta_does_not_overwrite_existing_memoir() -> None:
    state = ThreadState()
    state.project.genre.value = "memoir"
    state.project.genre.confidence = 0.90
    state.project.genre.source = Source.USER_STATED

    updated = StateApplier().apply(
        state,
        _extraction(
            "project.genre",
            "fiction",
            confidence=0.90,
            source=Source.USER_STATED,
        ),
    )

    assert updated.project.genre.value == "memoir"


def test_lower_confidence_genre_delta_does_not_overwrite_existing_memoir() -> None:
    state = ThreadState()
    state.project.genre.value = "memoir"
    state.project.genre.confidence = 0.90
    state.project.genre.source = Source.USER_STATED

    updated = StateApplier().apply(
        state,
        _extraction(
            "project.genre",
            "fiction",
            confidence=0.80,
            source=Source.USER_STATED,
        ),
    )

    assert updated.project.genre.value == "memoir"


def test_higher_confidence_genre_delta_overwrites_existing_fiction() -> None:
    state = ThreadState()
    state.project.genre.value = "fiction"
    state.project.genre.confidence = 0.70
    state.project.genre.source = Source.USER_STATED

    updated = StateApplier().apply(
        state,
        _extraction(
            "project.genre",
            "memoir",
            confidence=0.95,
            source=Source.USER_STATED,
        ),
    )

    assert updated.project.genre.value == "memoir"


def test_lower_confidence_status_delta_does_not_overwrite_completed_draft() -> None:
    state = ThreadState()
    state.project.manuscript_status.value = "completed_draft"
    state.project.manuscript_status.confidence = 0.90
    state.project.manuscript_status.source = Source.USER_STATED

    updated = StateApplier().apply(
        state,
        _extraction(
            "project.manuscript_status",
            "idea_only",
            confidence=0.80,
            source=Source.USER_STATED,
        ),
    )

    assert updated.project.manuscript_status.value == "completed_draft"


def test_empty_manuscript_status_is_set_by_incoming_delta() -> None:
    updated = StateApplier().apply(
        ThreadState(),
        _extraction(
            "project.manuscript_status",
            "completed_draft",
            confidence=0.70,
            source=Source.AI_EXTRACTED,
        ),
    )

    assert updated.project.manuscript_status.value == "completed_draft"


def test_lower_confidence_word_count_delta_does_not_overwrite_existing_value() -> None:
    state = ThreadState()
    state.project.word_count.value = 50000
    state.project.word_count.confidence = 0.90
    state.project.word_count.source = Source.USER_STATED

    updated = StateApplier().apply(
        state,
        _extraction(
            "project.word_count",
            65000,
            confidence=0.80,
            source=Source.USER_STATED,
        ),
    )

    assert updated.project.word_count.value == 50000


def test_empty_page_count_is_set_by_incoming_delta() -> None:
    updated = StateApplier().apply(
        ThreadState(),
        _extraction(
            "project.page_count",
            120,
            confidence=0.50,
            source=Source.AI_EXTRACTED,
        ),
    )

    assert updated.project.page_count.value == 120


def test_explicit_correction_source_overwrites_existing_durable_genre() -> None:
    state = ThreadState()
    state.project.genre.value = "fiction"
    state.project.genre.confidence = 0.95
    state.project.genre.source = Source.USER_STATED

    updated = StateApplier().apply(
        state,
        _extraction(
            "project.genre",
            "memoir",
            confidence=0.80,
            source=Source.USER_CORRECTED,
            raw_excerpt="Actually, it's memoir, not fiction",
        ),
    )

    assert updated.project.genre.value == "memoir"
    assert updated.project.genre.source == Source.USER_CORRECTED


def _extraction(
    path: str,
    value: object,
    *,
    confidence: float,
    source: Source,
    raw_excerpt: str | None = None,
) -> CombinedExtraction:
    return CombinedExtraction(
        state_deltas=[
            StateDelta(
                path=path,
                value=value,
                confidence=confidence,
                source=source,
                extracted_by="test",
                raw_excerpt=raw_excerpt,
            )
        ]
    )
