from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.domain.enums import ContactMethod, ManuscriptStatus, SalesStage, ServiceCategory
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
    lead_created: bool = False
    lead_id: str | None = None
    lead_intake_payload: dict[str, Any] = Field(default_factory=dict)
