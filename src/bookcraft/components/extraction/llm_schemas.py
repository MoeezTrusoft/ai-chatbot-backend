from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExtractedValue(BaseModel):
    """A single value extracted by the LLM with per-field confidence and provenance."""

    model_config = ConfigDict(extra="forbid")

    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_quote: str = ""  # verbatim phrase from user message that justifies this extraction


class LLMExtractedFacts(BaseModel):
    """Structured output of the LLM metadata extraction pass.

    Fields that map to FieldMeta state paths are converted to StateDelta objects.
    Rich free-text fields (cover_preferences, section_structure, page_dimensions)
    are stored in state.service_metadata["book_specs"].
    """

    model_config = ConfigDict(extra="ignore")

    # Personal info → personal.* FieldMeta paths
    name: ExtractedValue | None = None
    email: ExtractedValue | None = None
    phone: ExtractedValue | None = None
    preferred_contact_method: ExtractedValue | None = None
    timezone: ExtractedValue | None = None

    # Project info → project.* FieldMeta paths
    book_title: ExtractedValue | None = None
    genre: ExtractedValue | None = None
    sub_genre: ExtractedValue | None = None
    word_count: ExtractedValue | None = None
    page_count: ExtractedValue | None = None
    manuscript_status: ExtractedValue | None = None
    target_completion_date: ExtractedValue | None = None

    # Commercial info → commercial.* FieldMeta paths
    budget_range: ExtractedValue | None = None
    timeline: ExtractedValue | None = None

    # Service interest — stored as ServiceInterest, not a FieldMeta path
    service_interest: ExtractedValue | None = None

    # Rich free-text metadata — no FieldMeta path, stored in service_metadata["book_specs"]
    page_dimensions: ExtractedValue | None = None    # e.g. "5.5 x 8.5 inches"
    cover_preferences: ExtractedValue | None = None  # e.g. "dark cover with forest imagery"
    section_structure: ExtractedValue | None = None  # e.g. "6 sections, ~50 poems total"
    target_audience: ExtractedValue | None = None    # e.g. "young adults aged 14–18"

    # Coreference notes — LLM explains any reference resolution it performed
    coreference_notes: list[str] = Field(default_factory=list)
