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

