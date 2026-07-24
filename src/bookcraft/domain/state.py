from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bookcraft.domain.enums import (
    ContactMethod,
    ManuscriptStatus,
    SalesStage,
    ServiceCategory,
    coerce_manuscript_status,
)
from bookcraft.domain.meta import FieldMeta


class PersonalInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    email: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    phone: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    preferred_contact_method: FieldMeta[ContactMethod] = Field(
        default_factory=FieldMeta[ContactMethod]
    )
    timezone: FieldMeta[str] = Field(default_factory=FieldMeta[str])


class ServiceInterest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: FieldMeta[ServiceCategory] = Field(default_factory=FieldMeta[ServiceCategory])
    subservices: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ProjectInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    genre: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    sub_genre: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    synopsis: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    word_count: FieldMeta[int] = Field(default_factory=FieldMeta[int])
    page_count: FieldMeta[int] = Field(default_factory=FieldMeta[int])
    manuscript_status: FieldMeta[ManuscriptStatus] = Field(
        default_factory=FieldMeta[ManuscriptStatus]
    )
    target_completion_date: FieldMeta[datetime] = Field(default_factory=FieldMeta[datetime])
    services_discussed: list[ServiceInterest] = Field(default_factory=list)
    # Coherence / assumption-guard fields (PR: conversation-coherence).
    genre_status: str | None = None  # "uncertain" | "confirmed" | None
    genre_candidates: list[str] = Field(default_factory=list)
    book_formats: list[str] = Field(default_factory=list)  # e.g. ["picture_book"]
    audience: str | None = None  # e.g. "children" — only when explicitly evidenced

    @field_validator("manuscript_status", mode="before")
    @classmethod
    def _coerce_manuscript_status(cls, value: Any) -> Any:
        """Coerce legacy / coarse status strings to the canonical enum on load.

        Persisted threads may carry pre-fix values (e.g. ``not_started``) that
        are no longer valid enum members. Without this, ``model_validate`` raises
        and every subsequent turn of that thread 500s. We rewrite the inner
        ``value`` to a canonical status (or ``None`` when unmappable) so the
        thread keeps loading instead of becoming permanently wedged.
        """
        if not isinstance(value, dict):
            return value
        inner = value.get("value")
        if inner is None:
            return value
        coerced = coerce_manuscript_status(inner)
        if coerced is None and inner not in (None, ""):
            # Unmappable legacy value: clear it rather than crash on load.
            return {**value, "value": None}
        if coerced is not None and coerced.value != inner:
            return {**value, "value": coerced.value}
        return value


class CommercialInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget_range: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    timeline_expectation: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    latest_quote_id: FieldMeta[str] = Field(default_factory=FieldMeta[str])


class DocumentsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nda_requested: FieldMeta[bool] = Field(default_factory=FieldMeta[bool])
    agreement_requested: FieldMeta[bool] = Field(default_factory=FieldMeta[bool])
    latest_nda_document_id: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    latest_agreement_document_id: FieldMeta[str] = Field(default_factory=FieldMeta[str])


class LeadActionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_id: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    preferred_contact_method: str | None = None
    created: bool = False
    last_updated_at: datetime | None = None


class ConsultationActionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: bool = False
    pending_confirmation: bool = False
    pending_slot: dict[str, Any] | None = None
    confirmed_appointment_id: str | None = None
    customer_timezone: str | None = None
    csr_id: str | None = None
    csr_name: str | None = None
    preferred_date: str | None = None
    preferred_time_window: str | None = None
    duration_minutes: int = 30
    # Authoritative confirmation facts captured at booking time. Once set, the
    # response layer grounds every later mention of the appointment on these exact
    # values so the LLM cannot drift the date/time/CSR (audit C1: chat 6070).
    confirmed_display_time: str | None = None
    confirmed_customer_display_time: str | None = None


class PricingActionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: bool = False
    quote_id: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    quote_attempt_count: int = 0
    used_default_assumptions: bool = False
    assumptions: dict[str, Any] | None = None
    last_quote_summary: str | None = None


class PortfolioActionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: bool = False
    requested_service: str | None = None
    genre: str | None = None
    seen_sample_ids: list[str] = Field(default_factory=list)
    last_sample_ids: list[str] = Field(default_factory=list)


class DocumentActionDetailState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: bool = False
    document_id: str | None = None
    delivery_status: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    effective_date: str | None = None
    required_quote_id: str | None = None


class DocumentActionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nda: DocumentActionDetailState = Field(default_factory=DocumentActionDetailState)
    agreement: DocumentActionDetailState = Field(default_factory=DocumentActionDetailState)


class PendingConfirmationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None


class SalesActionsState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead: LeadActionState = Field(default_factory=LeadActionState)
    consultation: ConsultationActionState = Field(default_factory=ConsultationActionState)
    pricing: PricingActionState = Field(default_factory=PricingActionState)
    portfolio: PortfolioActionState = Field(default_factory=PortfolioActionState)
    documents: DocumentActionState = Field(default_factory=DocumentActionState)
    pending_confirmation: PendingConfirmationState = Field(default_factory=PendingConfirmationState)


class ThreadState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    sales_stage: FieldMeta[SalesStage] = Field(default_factory=FieldMeta[SalesStage])
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    commercial: CommercialInfo = Field(default_factory=CommercialInfo)
    documents: DocumentsInfo = Field(default_factory=DocumentsInfo)
    sales_actions: SalesActionsState = Field(default_factory=SalesActionsState)
    rolling_summary: str = ""
    # Multi-project context — serialised ProjectContext dicts, one per book/project.
    conversation_projects: list[dict[str, Any]] = Field(default_factory=list)
    # Slot resolution statuses — serialised SlotResolutionStatus dicts.
    slot_resolution_statuses: list[dict[str, Any]] = Field(default_factory=list)
    # Portfolio filter tracking — controls ask-once / fallback behaviour.
    portfolio_filter_state: dict[str, Any] = Field(default_factory=dict)
    # Attachment intake state (Phase 13).
    attachments_received: list[dict[str, Any]] = Field(default_factory=list)
    latest_assessment_type: str | None = None
    latest_specialist_role: str | None = None
    # Lead objective state (Phase 13 / PR 2).
    lead_objective_stage: str | None = None
    contact_info: dict[str, Any] = Field(default_factory=dict)
    # Per-field capture status: name/email/phone → given | not_given | unavailable.
    # Only "not_given" keeps the bot asking; "unavailable" (customer said they can't
    # provide it) is sticky and lets a consultation proceed on the other channel.
    contact_status: dict[str, str] = Field(default_factory=dict)
    lead_created: bool = False
    lead_id: str | None = None
    lead_intake_payload: dict[str, Any] = Field(default_factory=dict)
    # Language guard — segments ignored due to non-English content in mixed messages.
    language_ignored_segments: list[dict[str, Any]] = Field(default_factory=list)
    # Pending interaction context for coherent-reply resolution.
    pending_slots: list[str] = Field(default_factory=list)
    pending_question: dict[str, Any] = Field(default_factory=dict)
    # Safety event log — used by input_guard to track recent hostility events.
    safety_events: list[dict[str, Any]] = Field(default_factory=list)
    # Consultation-first sales planner (PR 2).
    consultation_stage: str | None = None  # engaging → consultation_pending → etc.
    preferred_call_time: str | None = None  # e.g. "tomorrow afternoon", "Friday 3pm"
    preferred_timezone: str | None = None
    # "sms" when the customer asked to be texted rather than called.
    preferred_contact_channel: str | None = None
    # Customer has a usable phone but declined a voice CALL ("can they text, I'm
    # bad at calling"). Distinct from contact_status["phone"] == "unavailable",
    # which means we can't reach them on that number at all. Sticky until they
    # ask for a call. Suppresses the call-time booking loop (chat 6816).
    call_opt_out: bool = False
    # Customer postponed the engagement ("not doing it until next month"). Sticky
    # until they re-engage; stops the bot re-opening the day/time ask every turn.
    consultation_deferred: bool = False
    consultation_defer_hint: str | None = None  # verbatim cue, e.g. "next month"
    current_question_type: str | None = None  # last detected priority question type
    answer_before_capture_applied: bool = False
    # Service metadata (PR 4) — extracted from conversation.
    publishing_platforms: list[str] = Field(default_factory=list)
    target_retailers: list[str] = Field(default_factory=list)
    isbn_status: str | None = None  # has_isbn | needs_isbn | not_sure | None
    distribution_goal: str | None = None
    service_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metadata_candidates: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    # Batch 2 Step 16: consultation handoff guard — prevents retrigger on unrelated turns.
    consultation_handoff_created: bool = False
    consultation_handoff_action_id: str | None = None
    # Batch 3 Step 4: lead created acknowledgment guard — prevents looping on confirmation.
    lead_created_acknowledged: bool = False
    # Step 2 (tone fix): last prior turn stored so the LLM has conversation context.
    # Stored as normalized/redacted text — not raw PII-bearing user input.
    last_user_message: str = ""
    last_assistant_text: str = ""
    # Context-management advisory item #1: rolling window of the last 5 (user,
    # assistant) exchanges so the LLM stays coherent across several turns, not
    # just the single prior pair above. Each side stored normalized/redacted and
    # truncated to 300 chars (same PII handling as last_user_message). Pydantic
    # coerces the persisted JSON arrays back into tuples on load.
    recent_turns: list[tuple[str, str]] = Field(default_factory=list)
    # Highest realtime turn token persisted for this thread. Shared across workers
    # via the thread store so a superseded (lower-token) turn — aborted by the
    # realtime layer when a concatenated burst is re-sent — never overwrites a newer
    # turn's state. Mediated by optimistic-locking on save.
    latest_turn_token: int = 0
    # Step 3 (tone fix): track whether the bot asked for contact in the last turn,
    # so LeadObjectiveEngine can back off when the user deflects.
    last_turn_asked_contact: bool = False
    # Contact enrichment: set after the bot has asked once for the missing second
    # contact method (phone or email). Prevents asking more than once.
    contact_second_method_requested: bool = False
    # Persona: assigned BookCraft representative name for this thread.
    # Set on the first turn; reused for the rest of the conversation.
    representative_name: str | None = None
    # Last detected message language for this thread. Fed back to the language guard
    # as cached_language so short follow-ups after a non-English turn stay consistent
    # instead of defaulting to English (audit B5 — no per-thread language stickiness).
    detected_language: str | None = None
    # User messages seen since the English-only redirect ("Language Unavailable") was
    # last sent. The redirect is rate-limited to at most once per N user messages
    # (see chat.py) so a run of non-English turns gets the notice occasionally, not on
    # every message. Counts ALL user messages (any language). Defaults high so the very
    # first non-English message always sends; capped at the threshold thereafter.
    msgs_since_language_redirect: int = 10
    # CSR handover context — populated when a CSR takes over and sends messages.
    csr_handover_active: bool = False
    csr_context_abstract: str = ""        # LLM-compressed summary of oldest CSR turns
    csr_context_recent_verbatim: list[dict[str, str]] = Field(default_factory=list)
    csr_turns_ingested: int = 0
    csr_handover_returned_at: datetime | None = None
    # Typed CSR commitments (price, timeline) — never mixed into the narrative summary.
    csr_commitments: list[dict[str, Any]] = Field(default_factory=list)
