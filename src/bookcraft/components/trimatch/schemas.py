from __future__ import annotations

import re as _re
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory


class TriMatchDimension(StrEnum):
    QUERY_INTENT = "query_intent"
    SERVICE_INTENT = "service_intent"
    FUNNEL_STAGE = "funnel_stage"


class TriMatchLayer(StrEnum):
    EXACT = "exact"
    REGEX = "regex"
    PATTERN = "pattern"
    SEMANTIC = "semantic"
    FUZZY = "fuzzy"


class TriMatchMode(StrEnum):
    SHADOW = "shadow"
    VOTE_ONLY = "vote_only"
    SHORTCUT_ENABLED = "shortcut_enabled"


class RuleTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_intent: QueryIntentType | None = None
    service_intent: ServiceCategory | None = None
    funnel_stage: SalesStage | None = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> RuleTarget:
        values = [self.query_intent, self.service_intent, self.funnel_stage]
        if sum(value is not None for value in values) != 1:
            raise ValueError("rule target must set exactly one dimension")
        return self

    @property
    def dimension(self) -> TriMatchDimension:
        if self.query_intent is not None:
            return TriMatchDimension.QUERY_INTENT
        if self.service_intent is not None:
            return TriMatchDimension.SERVICE_INTENT
        return TriMatchDimension.FUNNEL_STAGE

    @property
    def value(self) -> str:
        return str(self.query_intent or self.service_intent or self.funnel_stage)


class TriMatchRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3)
    layer: TriMatchLayer
    target: RuleTarget
    phrases: list[str] = Field(default_factory=list)
    regex: str | None = None
    pattern: list[str] = Field(default_factory=list)
    semantic_examples: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    enabled: bool = True
    shortcut_allowed: bool = False

    @model_validator(mode="after")
    def validate_payload_for_layer(self) -> TriMatchRule:
        if self.layer == TriMatchLayer.EXACT and not self.phrases:
            raise ValueError("exact rule requires phrases")
        if self.layer == TriMatchLayer.REGEX and not self.regex:
            raise ValueError("regex rule requires regex")
        if self.layer == TriMatchLayer.PATTERN and not self.pattern:
            raise ValueError("pattern rule requires pattern")
        if self.layer == TriMatchLayer.SEMANTIC and not self.semantic_examples:
            raise ValueError("semantic rule requires semantic_examples")
        if self.layer in {TriMatchLayer.SEMANTIC, TriMatchLayer.FUZZY} and self.shortcut_allowed:
            raise ValueError("semantic and fuzzy rules cannot shortcut")
        return self


class RulePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    rules: list[TriMatchRule]

    @field_validator("rules")
    @classmethod
    def unique_rule_ids(cls, rules: list[TriMatchRule]) -> list[TriMatchRule]:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Tri-Match rule id")
        return rules


@_dataclass
class CompiledRulePack:
    """A RulePack with pre-compiled indexes for faster matching.

    Wraps a RulePack and adds:
    - EXACT layer: union regex for all phrase sets
    - REGEX layer: pre-compiled pattern objects
    - PATTERN layer: first-token lookup index
    - Embeddings: pre-computed for SEMANTIC layer (populated separately)
    """

    rule_pack: RulePack

    # EXACT layer: single compiled union regex per rule_id -> set of phrase triggers
    exact_union_pattern: "_re.Pattern[str] | None" = _field(default=None)
    # Map from rule_id to the rule itself for fast lookup after regex match
    exact_rule_by_id: "dict[str, TriMatchRule]" = _field(default_factory=dict)

    # REGEX layer: pre-compiled patterns per rule
    compiled_regex: "dict[str, _re.Pattern[str]]" = _field(default_factory=dict)

    # PATTERN layer: first-token -> list[rule_id] for fast candidate pruning
    pattern_first_token_index: "dict[str, list[str]]" = _field(default_factory=dict)

    # SEMANTIC layer: per-rule embeddings, shape (n_rules, embedding_dim)
    # List of (rule_id, embedding_vector) tuples
    semantic_embeddings: "list[tuple[str, list[float]]]" = _field(default_factory=list)
    # Map from rule_id -> embedding index
    semantic_rule_index: "dict[str, int]" = _field(default_factory=dict)


class TriMatchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    dimension: TriMatchDimension
    target: str
    layer: TriMatchLayer
    matched_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    negated: bool = False
    hedged: bool = False
    counterfactual: bool = False
    shortcut_eligible: bool = False


class TriMatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_primary: QueryIntentType | None = None
    service_primary: ServiceCategory | None = None
    service_secondary: list[ServiceCategory] = Field(default_factory=list)
    funnel_stage: SalesStage | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[TriMatchEvidence] = Field(default_factory=list)
    mode: TriMatchMode
    shadow_only_dimensions: list[TriMatchDimension] = Field(default_factory=list)
    shortcut_eligible: bool = False


class EvalExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    dimension: TriMatchDimension
    expected: str
    subset: str = "default"


class TriMatchVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    precision: dict[str, float] = Field(default_factory=dict)
    recall: dict[str, float] = Field(default_factory=dict)


MatcherPayload = dict[Literal["text"], str]
