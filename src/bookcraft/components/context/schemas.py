from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.attachments.intake import ChatAttachment
from bookcraft.components.context.delegation import SlotResolutionStatus


class KnownFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    label: str
    value: str | int | float | bool
    confidence: float
    source: str
    raw_excerpt: str | None = None


class ContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_facts: list[KnownFact] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    forbidden_reasks: list[str] = Field(default_factory=list)
    active_service: str | None = None
    active_genre: str | None = None
    manuscript_status: str | None = None
    sales_stage: str | None = None
    outstanding_questions: list[str] = Field(default_factory=list)
    repeated_user_info: list[str] = Field(default_factory=list)
    contradiction_warnings: list[str] = Field(default_factory=list)
    allowed_next_questions: list[str] = Field(default_factory=list)
    disallowed_next_questions: list[str] = Field(default_factory=list)
    response_hint: str | None = None
    # Project context fields (populated when ProjectContextManager is active).
    active_project_id: str | None = None
    project_event: str | None = None
    previous_project_id: str | None = None
    project_memory_summary: list[str] = Field(default_factory=list)
    # Phase 12 PR 7: richer project context.
    active_project_label: str | None = None
    previous_project_summary: list[str] = Field(default_factory=list)
    project_scope_warnings: list[str] = Field(default_factory=list)
    # Slot resolution fields (populated by SlotTracker / ContextPackBuilder).
    declined_slots: list[SlotResolutionStatus] = Field(default_factory=list)
    delegated_slots: list[SlotResolutionStatus] = Field(default_factory=list)
    unknown_slots: list[SlotResolutionStatus] = Field(default_factory=list)
    # Phase 13: attachment intake fields.
    attachments_received: list[ChatAttachment] = Field(default_factory=list)
    assessment_type: str | None = None
    specialist_role: str | None = None
    attachment_policy: str = "metadata_only_no_content_analysis"
    lead_objective_stage: str | None = None
    # Manuscript upload pitch: True when the author has written content but has
    # not yet uploaded a file. Drives a one-time free editorial assessment pitch.
    manuscript_upload_eligible: bool = False
    contact_capture_status: str | None = None
    contact_complete: bool = False  # name + email + phone all present
    # Per-field capture status (name/email/phone → given|not_given|unavailable).
    # A field marked "unavailable" must not be re-solicited (chat 6759).
    contact_status: dict[str, str] = Field(default_factory=dict)
    lead_created: bool = False
    # Coherence / assumption-guard fields (PR: conversation-coherence).
    genre_status: str | None = None  # "uncertain" | "confirmed" | None
    genre_candidates: list[str] = Field(default_factory=list)
    book_formats: list[str] = Field(default_factory=list)
    audience: str | None = None
    pending_slots: list[str] = Field(default_factory=list)
    preferred_call_time: str | None = None
    # Concrete half-hour openings to offer when the customer's time is indefinite.
    suggested_call_slots: list[str] = Field(default_factory=list)
    language_ignored_segments: list[dict[str, str]] = Field(default_factory=list)
    assumption_warnings: list[str] = Field(default_factory=list)
    # Greeting intent guard.
    is_greeting_turn: bool = False
    # Consultation-first sales planner (PR 2).
    consultation_stage: str | None = None
    current_question_type: str | None = None
    answer_before_capture_applied: bool = False
    # Service metadata (PR 4).
    publishing_platforms: list[str] = Field(default_factory=list)
    target_retailers: list[str] = Field(default_factory=list)
    isbn_status: str | None = None
    distribution_goal: str | None = None
    service_metadata: dict[str, dict[str, object]] = Field(default_factory=dict)
    metadata_candidates: dict[str, list[dict[str, object]]] = Field(default_factory=dict)
    available_service_metadata_keys: list[str] = Field(default_factory=list)
    metadata_missing_for_active_service: list[str] = Field(default_factory=list)
    metadata_confidence_warnings: list[str] = Field(default_factory=list)
    # Context enforcement (PR: context-enforcement-correction-recovery).
    negated_services: list[str] = Field(default_factory=list)
    negated_platforms: list[str] = Field(default_factory=list)
    negated_formats: list[str] = Field(default_factory=list)
    stale_context_terms: list[str] = Field(default_factory=list)
    context_enforcement_warnings: list[str] = Field(default_factory=list)
