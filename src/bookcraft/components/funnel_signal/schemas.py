from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bookcraft.components.trimatch.schemas import TriMatchLayer
from bookcraft.domain.enums import SalesStage


class FunnelPartition(StrEnum):
    USER_LANGUAGE = "user_language"
    CRM = "crm"
    DROPPED = "dropped"


class FunnelRawRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3)
    section: str = Field(min_length=1)
    stage: SalesStage
    layer: TriMatchLayer = TriMatchLayer.EXACT
    phrases: list[str] = Field(default_factory=list)
    regex: str | None = None
    pattern: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("phrases", "pattern")
    @classmethod
    def strip_blank_items(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @model_validator(mode="after")
    def validate_layer_payload(self) -> FunnelRawRule:
        if self.layer == TriMatchLayer.EXACT and not self.phrases:
            raise ValueError("exact funnel rule requires phrases")
        if self.layer == TriMatchLayer.REGEX and not self.regex:
            raise ValueError("regex funnel rule requires regex")
        if self.layer == TriMatchLayer.PATTERN and not self.pattern:
            raise ValueError("pattern funnel rule requires pattern")
        if self.layer in {TriMatchLayer.SEMANTIC, TriMatchLayer.FUZZY}:
            raise ValueError("funnel imports support exact, regex, and pattern layers only")
        return self


class DroppedFunnelRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: FunnelRawRule
    reason: str


class FunnelPartitionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_version: str
    user_language_rules: list[FunnelRawRule] = Field(default_factory=list)
    crm_rules: list[FunnelRawRule] = Field(default_factory=list)
    dropped_rules: list[DroppedFunnelRule] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def user_language_count(self) -> int:
        return len(self.user_language_rules)

    @property
    def crm_count(self) -> int:
        return len(self.crm_rules)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped_rules)
