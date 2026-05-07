from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.domain.enums import Source

T = TypeVar("T")


class FieldMeta(BaseModel, Generic[T]):  # noqa: UP046 - Pydantic generic model syntax.
    model_config = ConfigDict(frozen=False)

    value: T | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Source = Source.SYSTEM
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extracted_by: str | None = None
    raw_excerpt: str | None = None

    def is_high_confidence(self, threshold: float = 0.85) -> bool:
        trusted_source = self.source in {
            Source.USER_STATED,
            Source.USER_CONFIRMED,
            Source.CSR_ENTERED,
        }
        return self.value is not None and (trusted_source or self.confidence >= threshold)
