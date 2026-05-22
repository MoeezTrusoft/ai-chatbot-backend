from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter

from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.domain.enums import Source
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


REJECTED_DELTAS = Counter(
    "extraction_rejected_deltas_total",
    "State deltas rejected due to invalid path.",
    ["reason"],
)


class StateApplier:
    def apply(
        self,
        state: ThreadState,
        extraction: CombinedExtraction,
        *,
        rejected_paths: list[str] | None = None,
    ) -> ThreadState:
        updated = state.model_copy(deep=True)
        for delta in extraction.state_deltas:
            try:
                self._apply_delta(updated, delta)
            except (ValueError, AttributeError) as exc:
                # Step 6: safe path handling — log and skip rather than crash.
                reason = type(exc).__name__
                REJECTED_DELTAS.labels(reason=reason).inc()
                if rejected_paths is not None:
                    rejected_paths.append(delta.path)
        return updated

    def _apply_delta(self, state: ThreadState, delta: StateDelta) -> None:
        current = self._get_field(state, delta.path)
        if not should_apply_delta(current, delta):
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
        parts = path.split(".", 1)
        if len(parts) != 2:  # noqa: PLR2004
            msg = f"StateDelta path must be 'owner.field', got: {path!r}"
            raise ValueError(msg)
        owner_name, field_name = parts
        owner = getattr(state, owner_name, None)
        if owner is None:
            msg = f"State has no attribute {owner_name!r} (path={path!r})"
            raise AttributeError(msg)
        field = getattr(owner, field_name, None)
        if not isinstance(field, FieldMeta):
            msg = f"State path does not point to FieldMeta: {path}"
            raise ValueError(msg)
        return field

    @staticmethod
    def _set_field(state: ThreadState, path: str, value: FieldMeta[Any]) -> None:
        owner_name, field_name = path.split(".", 1)
        owner = getattr(state, owner_name)
        setattr(owner, field_name, value)


def should_apply_delta(existing: FieldMeta[Any] | None, incoming: StateDelta) -> bool:
    if existing is None or existing.value is None:
        return True

    # Explicit user corrections are allowed to replace durable facts even
    # when their extraction confidence is lower than the existing field.
    if incoming.source == Source.USER_CORRECTED:
        return True

    # Otherwise, existing state wins ties. A derived or repeated fact must be
    # strictly more confident before it can replace what the thread remembers.
    return incoming.confidence > existing.confidence
