from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.domain.enums import Source


class StateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    value: object
    confidence: float = Field(ge=0.0, le=1.0)
    source: Source
    extracted_by: str
    raw_excerpt: str | None = None


class ContactExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    preferred_contact_method: str | None = None
    preferred_contact_time: str | None = None


class ProjectExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_title: str | None = None
    genre: str | None = None
    manuscript_status: str | None = None
    word_count: int | None = None
    page_count: int | None = None
    target_format: str | None = None
    target_publishing_platforms: list[str] = Field(default_factory=list)
    target_launch_window: str | None = None
    author_goal: str | None = None


class CommercialExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget_stated: str | None = None
    urgency_stated: str | None = None
    selected_services: list[str] = Field(default_factory=list)
    add_ons: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    quote_accepted: bool = False


class ServiceInterestExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    services: list[str] = Field(default_factory=list)


class SampleRequestExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: bool = False
    service: str | None = None
    genre: str | None = None


class DocumentRequestExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_type: Literal["nda", "agreement"] | None = None


class ConsultationRequestExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: bool = False
    # Enriched fields added by deterministic consultation extractor.
    requested_date_text: str | None = None  # e.g. "tomorrow", "Friday"
    requested_time_text: str | None = None  # e.g. "4pm", "morning"
    requested_datetime_text: str | None = None  # combined, e.g. "tomorrow at 4pm"
    timezone_text: str | None = None  # e.g. "PST", "PKT", "EST"
    channel_preference: str | None = None  # e.g. "phone", "zoom", "email"
    timezone_unknown: bool = False  # True when relative time given, no tz known


class CombinedExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact: ContactExtraction = Field(default_factory=ContactExtraction)
    project: ProjectExtraction = Field(default_factory=ProjectExtraction)
    commercial: CommercialExtraction = Field(default_factory=CommercialExtraction)
    service_interest: ServiceInterestExtraction = Field(default_factory=ServiceInterestExtraction)
    sample_request: SampleRequestExtraction = Field(default_factory=SampleRequestExtraction)
    document_request: DocumentRequestExtraction = Field(default_factory=DocumentRequestExtraction)
    consultation_request: ConsultationRequestExtraction = Field(
        default_factory=ConsultationRequestExtraction
    )
    user_questions: list[str] = Field(default_factory=list)
    state_deltas: list[StateDelta] = Field(default_factory=list)
