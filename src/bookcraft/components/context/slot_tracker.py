from __future__ import annotations

from typing import TYPE_CHECKING

from bookcraft.components.context.delegation import (
    DelegatedDecision,
    DelegatedDecisionDetector,
    SlotResolutionStatus,
    load_slot_statuses,
)

if TYPE_CHECKING:
    from bookcraft.components.context.schemas import ContextPack
    from bookcraft.domain.state import ThreadState

_DETECTOR = DelegatedDecisionDetector()


class SlotTracker:
    """Detects and merges per-slot delegation/declination decisions into thread state."""

    def __init__(self) -> None:
        self.last_decision: DelegatedDecision | None = None

    def update(
        self,
        *,
        text: str,
        state: ThreadState,
        current_slot: str | None = None,
        response_plan_next_question: str | None = None,
        context_pack: ContextPack | None = None,
        turn_id: str | None = None,
    ) -> list[SlotResolutionStatus]:
        """Run detection and return the full merged list of slot statuses.

        Returns the complete up-to-date list; the caller should persist it to
        state.slot_resolution_statuses.  Returns an empty list when no new
        delegation signal is detected in this turn.
        """
        decision = _DETECTOR.detect(
            text=text,
            current_slot=current_slot,
            response_plan_next_question=response_plan_next_question,
            context_pack=context_pack,
        )
        self.last_decision = decision

        if not decision.detected or decision.target_slot is None:
            return []

        # Attach active project_id so statuses are project-scoped.
        active_project_id: str | None = None
        conv_projects = getattr(state, "conversation_projects", None) or []
        for proj in conv_projects:
            if isinstance(proj, dict) and proj.get("active"):
                active_project_id = proj.get("project_id")
                break

        new_status = SlotResolutionStatus(
            slot=decision.target_slot,
            status=decision.status,
            source_turn_id=turn_id,
            reason=decision.cue,
            forbidden_reask=True,
            confidence=decision.confidence,
            project_id=active_project_id,
        )

        raw = getattr(state, "slot_resolution_statuses", None) or []
        existing = load_slot_statuses(raw)
        # Replace same slot for the same project (or legacy).
        merged = [
            s
            for s in existing
            if not (
                s.slot == decision.target_slot
                and (s.project_id == active_project_id or s.project_id is None)
            )
        ]
        merged.append(new_status)
        return merged
