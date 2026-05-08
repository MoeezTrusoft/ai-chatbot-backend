from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory


class IntentVote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_primary: QueryIntentType
    query_secondary: list[QueryIntentType] = Field(default_factory=list)
    service_primary: ServiceCategory | None = None
    service_secondary: list[ServiceCategory] = Field(default_factory=list)
    funnel_stage: SalesStage
    needs_clarification: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence: list[str] = Field(default_factory=list)


class IntentProviderStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CIRCUIT_OPEN = "circuit_open"


class ProviderIntentVote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    status: IntentProviderStatus
    vote: IntentVote | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    error: str | None = None
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)


class DecisionLayerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_vote: IntentVote
    provider_votes: list[ProviderIntentVote]
    query_scores: dict[str, float] = Field(default_factory=dict)
    service_scores: dict[str, float] = Field(default_factory=dict)
    funnel_stage_scores: dict[str, float] = Field(default_factory=dict)
    needs_clarification: bool
    audit_trail: list[str] = Field(default_factory=list)
