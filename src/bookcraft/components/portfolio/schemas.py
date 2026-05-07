from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bookcraft.domain.enums import ServiceCategory


class PortfolioStatus(StrEnum):
    FOUND = "found"
    UNAVAILABLE_CONFIDENTIAL = "unavailable_confidential"
    UNAVAILABLE_PENDING = "unavailable_pending"
    NO_MATCH = "no_match"


class PortfolioMediaType(StrEnum):
    AMAZON_LINK = "amazon_link"
    COVER_IMAGE = "cover_image"
    VIDEO = "video"
    WEBSITE = "website"
    EXTERNAL_LINK = "external_link"


class PortfolioSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    service: ServiceCategory
    genre: str | None = None
    url: str | None = None
    cover_image: str | None = None
    media_type: PortfolioMediaType
    reason_selected: str
    source_id: str

    @field_validator("url", "cover_image")
    @classmethod
    def valid_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("sample links must be http(s)")
        return stripped


class PortfolioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceCategory
    genre: str | None = None
    limit: int = Field(default=3, ge=1, le=10)


class PortfolioResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceCategory
    requested_genre: str | None = None
    status: PortfolioStatus
    samples: list[PortfolioSample] = Field(default_factory=list)
    message: str
    registry_version: str
    matched_genre: str | None = None
    fallback_used: bool = False


class PortfolioVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    sample_count: int
    service_counts: dict[str, int]
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
