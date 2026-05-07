from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter

from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState

NO_OVERWRITE_SKIPS = Counter(
    "extraction_no_overwrite_skips_total",
    "State deltas skipped by no-overwrite rule.",
    ["field"],
)
EXTRACTION_CONFLICTS = Counter(
    "extraction_conflicts_total",
    "State delta conflicts detected.",
    ["field"],
)


class StateApplier:
    def apply(self, state: ThreadState, extraction: CombinedExtraction) -> ThreadState:
        updated = state.model_copy(deep=True)
        for delta in extraction.state_deltas:
            self._apply_delta(updated, delta)
        return updated

    def _apply_delta(self, state: ThreadState, delta: StateDelta) -> None:
        current = self._get_field(state, delta.path)
        current_wins = (
            current.value is not None
            and current.is_high_confidence()
            and current.confidence > delta.confidence
        )
        if current_wins:
            NO_OVERWRITE_SKIPS.labels(field=delta.path).inc()
            if current.value != delta.value:
                EXTRACTION_CONFLICTS.labels(field=delta.path).inc()
            return
        replacement = FieldMeta[Any](
            value=delta.value,
            confidence=delta.confidence,
            source=delta.source,
            extracted_at=datetime.now(UTC),
            extracted_by=delta.extracted_by,
            raw_excerpt=delta.raw_excerpt,
        )
        self._set_field(state, delta.path, replacement)

    @staticmethod
    def _get_field(state: ThreadState, path: str) -> FieldMeta[Any]:
        owner_name, field_name = path.split(".", 1)
        owner = getattr(state, owner_name)
        field = getattr(owner, field_name)
        if not isinstance(field, FieldMeta):
            msg = f"State path does not point to FieldMeta: {path}"
            raise ValueError(msg)
        return field

    @staticmethod
    def _set_field(state: ThreadState, path: str, value: FieldMeta[Any]) -> None:
        owner_name, field_name = path.split(".", 1)
        owner = getattr(state, owner_name)
        setattr(owner, field_name, value)
