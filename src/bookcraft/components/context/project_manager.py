from __future__ import annotations

import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.state import ThreadState

# ---------------------------------------------------------------------------
# Pattern groups (generic, reusable — not one-off per-case)
# ---------------------------------------------------------------------------

_NEW_PROJECT_RE = re.compile(
    r"\b(?:another\s+book|new\s+book|second\s+book|different\s+manuscript"
    r"|separate\s+project|new\s+project|my\s+other\s+book|for\s+this\s+new\s+one"
    r"|different\s+book|next\s+book|another\s+project)\b",
    re.IGNORECASE,
)

_PREVIOUS_PROJECT_RE = re.compile(
    r"\b(?:previous\s+book|earlier\s+book|old\s+project|first\s+book"
    r"|the\s+book\s+we\s+discussed\s+before|back\s+to\s+that\s+book"
    r"|back\s+to\s+the\s+(?:first|previous|earlier|old)\s+book)\b",
    re.IGNORECASE,
)

# Strong: explicit same-project reference.
_SAME_PROJECT_STRONG_RE = re.compile(
    r"\b(?:for\s+the\s+same\s+book|same\s+manuscript|same\s+project|same\s+book)\b",
    re.IGNORECASE,
)

# Weak: implies addition but doesn't specify same vs. new book.
_SAME_PROJECT_WEAK_RE = re.compile(
    r"\b(?:also|as\s+well|along\s+with|together\s+with|too)\b|\bplus\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ProjectContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    label: str | None = None
    active: bool = True
    service_focus: list[str] = Field(default_factory=list)
    known_facts: dict[str, str | int | float | bool] = Field(default_factory=dict)
    declined_slots: dict[str, str] = Field(default_factory=dict)
    delegated_slots: dict[str, str] = Field(default_factory=dict)
    created_turn_id: str | None = None
    last_active_turn_id: str | None = None


_ProjectEvent = Literal[
    "same_project",
    "new_project",
    "project_switch",
    "same_project_service_addition",
    "ambiguous_project_reference",
]


class ProjectShiftDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: _ProjectEvent
    active_project_id: str
    previous_project_id: str | None = None
    carry_over_allowed: bool = False
    confidence: float
    audit: list[str] = Field(default_factory=list)


class ProjectContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_project_id: str | None
    previous_project_id: str | None
    projects: list[ProjectContext]
    decision: ProjectShiftDecision
    audit: list[str] = Field(default_factory=list)

    @property
    def active_project_known_facts(self) -> dict[str, str | int | float | bool]:
        for p in self.projects:
            if p.active:
                return p.known_facts
        return {}


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ProjectContextManager:
    """Determines project shift events and maintains per-project context."""

    def decide(
        self,
        *,
        message: str,
        state: ThreadState,
        intent: IntentVote,
        context_pack: ContextPack | None = None,
        runtime_atoms: dict[str, Any] | None = None,
    ) -> ProjectContextSnapshot:
        del context_pack, runtime_atoms  # reserved for future enrichment

        projects = _load_projects(state)
        audit: list[str] = []

        has_new = bool(_NEW_PROJECT_RE.search(message))
        has_prev = bool(_PREVIOUS_PROJECT_RE.search(message))
        has_same_strong = bool(_SAME_PROJECT_STRONG_RE.search(message))
        has_same_weak = bool(_SAME_PROJECT_WEAK_RE.search(message))

        service = intent.service_primary.value if intent.service_primary is not None else None

        if has_new:
            audit.append("detect:new_project_marker")
        if has_prev:
            audit.append("detect:previous_project_marker")
        if has_same_strong:
            audit.append("detect:same_project_strong_marker")
        if has_same_weak:
            audit.append("detect:same_project_weak_marker")

        # First turn: no existing projects — create the default project.
        if not projects:
            new_project = ProjectContext(
                project_id=str(uuid4()),
                active=True,
                service_focus=[service] if service else [],
                known_facts=_snapshot_state_facts(state),
            )
            decision = ProjectShiftDecision(
                event="same_project",
                active_project_id=new_project.project_id,
                previous_project_id=None,
                carry_over_allowed=True,
                confidence=1.0,
                audit=audit + ["first_project_created"],
            )
            return ProjectContextSnapshot(
                active_project_id=new_project.project_id,
                previous_project_id=None,
                projects=[new_project],
                decision=decision,
                audit=["no_prior_projects"],
            )

        active_project = next((p for p in projects if p.active), projects[-1])
        current_active_id = active_project.project_id

        # --- New project ---
        if has_new:
            # Snapshot facts for the project being deactivated before switching away.
            if active_project is not None:
                deactivated = [
                    p.model_copy(update={
                        "active": False,
                        "known_facts": _snapshot_state_facts(state) if p.project_id == current_active_id else p.known_facts,
                    })
                    for p in projects
                ]
            else:
                deactivated = [p.model_copy(update={"active": False}) for p in projects]
            new_proj = ProjectContext(
                project_id=str(uuid4()),
                active=True,
                service_focus=[service] if service else [],
                known_facts={},  # fresh project starts with no inherited facts
            )
            return ProjectContextSnapshot(
                active_project_id=new_proj.project_id,
                previous_project_id=current_active_id,
                projects=deactivated + [new_proj],
                decision=ProjectShiftDecision(
                    event="new_project",
                    active_project_id=new_proj.project_id,
                    previous_project_id=current_active_id,
                    carry_over_allowed=False,
                    confidence=0.95,
                    audit=audit,
                ),
            )

        # --- Switch to previous project ---
        if has_prev:
            inactive = [p for p in projects if not p.active]
            if inactive:
                target = inactive[-1]  # most recently deactivated
                # Snapshot current project's facts before deactivation; restore target's preserved facts.
                updated = [
                    p.model_copy(update={
                        "active": p.project_id == target.project_id,
                        "known_facts": _snapshot_state_facts(state) if p.project_id == current_active_id else p.known_facts,
                    })
                    for p in projects
                ]
                return ProjectContextSnapshot(
                    active_project_id=target.project_id,
                    previous_project_id=current_active_id,
                    projects=updated,
                    decision=ProjectShiftDecision(
                        event="project_switch",
                        active_project_id=target.project_id,
                        previous_project_id=current_active_id,
                        carry_over_allowed=True,
                        confidence=0.9,
                        audit=audit,
                    ),
                )
            # Only one project exists — treat as same project.
            return ProjectContextSnapshot(
                active_project_id=current_active_id,
                previous_project_id=None,
                projects=list(projects),
                decision=ProjectShiftDecision(
                    event="same_project",
                    active_project_id=current_active_id,
                    previous_project_id=None,
                    carry_over_allowed=True,
                    confidence=0.7,
                    audit=audit + ["no_previous_project_found"],
                ),
            )

        # --- Same project, strong service-addition marker ---
        if has_same_strong:
            updated_focus = list(active_project.service_focus)
            if service and service not in updated_focus:
                updated_focus.append(service)
            updated_active = active_project.model_copy(update={"service_focus": updated_focus})
            updated = [updated_active if p.project_id == current_active_id else p for p in projects]
            return ProjectContextSnapshot(
                active_project_id=current_active_id,
                previous_project_id=None,
                projects=updated,
                decision=ProjectShiftDecision(
                    event="same_project_service_addition",
                    active_project_id=current_active_id,
                    previous_project_id=None,
                    carry_over_allowed=True,
                    confidence=0.9,
                    audit=audit,
                ),
            )

        # --- Weak service-addition marker with a known service → ambiguous ---
        if has_same_weak and service:
            return ProjectContextSnapshot(
                active_project_id=current_active_id,
                previous_project_id=None,
                projects=list(projects),
                decision=ProjectShiftDecision(
                    event="ambiguous_project_reference",
                    active_project_id=current_active_id,
                    previous_project_id=None,
                    carry_over_allowed=False,
                    confidence=0.5,
                    audit=audit,
                ),
            )

        # --- Default: same project continuation ---
        updated_focus = list(active_project.service_focus)
        if service and service not in updated_focus:
            updated_focus.append(service)
        updated_active = active_project.model_copy(update={"service_focus": updated_focus})
        updated = [updated_active if p.project_id == current_active_id else p for p in projects]
        return ProjectContextSnapshot(
            active_project_id=current_active_id,
            previous_project_id=None,
            projects=updated,
            decision=ProjectShiftDecision(
                event="same_project",
                active_project_id=current_active_id,
                previous_project_id=None,
                carry_over_allowed=True,
                confidence=0.85,
                audit=audit + ["default_same_project"],
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot_state_facts(state: ThreadState) -> dict[str, str | int | float | bool]:
    """Extract key conversation facts into a flat dict for ProjectContext.known_facts."""
    facts: dict[str, str | int | float | bool] = {}
    # Project facts
    if state.project.genre.value is not None:
        facts["project.genre"] = str(state.project.genre.value)
    if state.project.word_count.value is not None:
        facts["project.word_count"] = state.project.word_count.value
    if state.project.page_count.value is not None:
        facts["project.page_count"] = state.project.page_count.value
    if state.project.manuscript_status.value is not None:
        facts["project.manuscript_status"] = str(state.project.manuscript_status.value)
    # Contact facts (PII-safe keys only — no raw email/phone)
    if state.personal.name.value is not None:
        facts["contact.name"] = str(state.personal.name.value)
    # Service facts
    if state.project.services_discussed:
        facts["service.primary"] = str(state.project.services_discussed[-1].service.value)
    return facts


def _load_projects(state: ThreadState) -> list[ProjectContext]:
    raw: list[Any] = getattr(state, "conversation_projects", []) or []
    result: list[ProjectContext] = []
    for item in raw:
        if isinstance(item, dict):
            try:
                result.append(ProjectContext.model_validate(item))
            except Exception:  # noqa: BLE001,S110
                pass
        elif isinstance(item, ProjectContext):
            result.append(item)
    return result
