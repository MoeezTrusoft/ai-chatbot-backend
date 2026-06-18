from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RangeValue(BaseModel):
    """An extracted numeric range, e.g. word count '60,000-80,000' or budget '$500-$1,000'."""

    model_config = ConfigDict(extra="ignore")

    low: float | None = None
    high: float | None = None
    unit: str | None = None  # e.g. "words", "USD", "pages"
    confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    source_quote: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: Any) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.85

    def midpoint(self) -> float | None:
        """Return the midpoint for single-value approximation."""
        if self.low is not None and self.high is not None:
            return (self.low + self.high) / 2
        return self.low or self.high


class DimensionValue(BaseModel):
    """A structured dimension, e.g. '5.5 x 8.5 inches' for page dimensions."""

    model_config = ConfigDict(extra="ignore")

    width: float | None = None
    height: float | None = None
    unit: str | None = None  # e.g. "inches", "cm"
    confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    source_quote: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: Any) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.85


class ExtractedValue(BaseModel):
    """A single value extracted by the LLM with per-field confidence and provenance."""

    # extra="ignore" so unexpected sub-fields never raise ValidationError.
    # Confidence is coerced from string to float because Claude occasionally
    # returns "0.92" (string) instead of 0.92 (float), which previously caused
    # a ValidationError that silently dropped the entire extraction result.
    model_config = ConfigDict(extra="ignore")

    value: Any
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)
    source_quote: str = ""  # verbatim phrase from user message that justifies this extraction

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: Any) -> float:
        """Accept strings like '0.92' and clamp to [0, 1]."""
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.9  # safe fallback confidence


class ServiceMetadataItem(BaseModel):
    """One service-specific metadata fact (e.g. cover visual_style=photographic).

    ``key`` must be one of the active service's registry fields; ``value`` is validated
    against that field's accepted set by the extractor before it is stored.
    """

    model_config = ConfigDict(extra="ignore")

    key: str = ""
    value: Any = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    source_quote: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: Any) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.85


class LLMExtractedFacts(BaseModel):
    """Structured output of the LLM metadata extraction pass.

    Fields that map to FieldMeta state paths are converted to StateDelta objects.
    Rich free-text fields (cover_preferences, section_structure, page_dimensions)
    are stored in state.service_metadata["book_specs"].
    """

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def coerce_bare_values(cls, data: Any) -> Any:
        """Claude sometimes returns a bare string/number instead of an ExtractedValue dict.

        e.g. {"name": "Babar Azam"} instead of {"name": {"value": "Babar Azam", ...}}

        Wrap those bare values into a minimal ExtractedValue dict so field
        validation never raises a ValidationError for correctly identified facts.
        """
        if not isinstance(data, dict):
            return data
        _EV_FIELDS = {
            "name", "email", "phone", "preferred_contact_method", "timezone",
            "book_title", "genre", "sub_genre", "word_count", "page_count",
            "manuscript_status", "target_completion_date", "budget_range",
            "timeline", "service_interest", "page_dimensions", "cover_preferences",
            "section_structure", "target_audience",
        }
        for key, value in list(data.items()):
            # Fix coreference_notes: Claude returns "" (string) instead of [] (list).
            if key == "coreference_notes":
                if isinstance(value, str):
                    data[key] = [value] if value.strip() else []
                elif value is None:
                    data[key] = []
                continue

            if key not in _EV_FIELDS:
                continue
            if value is None:
                continue
            # If it's already a dict (proper ExtractedValue or RangeValue) leave it alone.
            if isinstance(value, dict):
                # Range-shaped dicts for word_count / budget_range must NOT be wrapped.
                if key in {"word_count", "budget_range"} and ("low" in value or "high" in value):
                    continue
                continue
            # Bare string / int / float → wrap into ExtractedValue shape.
            if isinstance(value, (str, int, float, bool)):
                data[key] = {
                    "value": value,
                    "confidence": 0.85,
                    "source_quote": str(value),
                }
        return data

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

    # Service-specific metadata for the active service (cover visual_style, editing_level,
    # platforms, etc.). Validated against the service registry by the extractor.
    service_metadata: list[ServiceMetadataItem] = Field(default_factory=list)

    # Coreference notes — LLM explains any reference resolution it performed
    coreference_notes: list[str] = Field(default_factory=list)

    # If True: the extracted facts are conditional on a future event (e.g. "if I get funding...")
    # The extractor sets this to signal the state applier should use lower confidence.
    conditional: bool = False
