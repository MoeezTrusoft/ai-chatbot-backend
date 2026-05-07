from datetime import UTC, datetime

from bookcraft.components.extraction import StateApplier
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.domain.enums import Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


def test_state_applier_does_not_overwrite_higher_confidence_user_data() -> None:
    state = ThreadState()
    state.personal.email = FieldMeta[str](
        value="confirmed@example.com",
        confidence=0.99,
        source=Source.USER_CONFIRMED,
        extracted_at=datetime.now(UTC),
    )
    extraction = CombinedExtraction(
        state_deltas=[
            StateDelta(
                path="personal.email",
                value="ai@example.com",
                confidence=0.7,
                source=Source.AI_EXTRACTED,
                extracted_by="mock_haiku",
            )
        ]
    )

    updated = StateApplier().apply(state, extraction)

    assert updated.personal.email.value == "confirmed@example.com"


def test_state_applier_sets_empty_fieldmeta() -> None:
    state = ThreadState()
    extraction = CombinedExtraction(
        state_deltas=[
            StateDelta(
                path="project.word_count",
                value=65000,
                confidence=0.96,
                source=Source.USER_STATED,
                extracted_by="deterministic_preextractor.v1",
            )
        ]
    )

    updated = StateApplier().apply(state, extraction)

    assert updated.project.word_count.value == 65000

