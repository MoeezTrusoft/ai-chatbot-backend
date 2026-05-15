from enum import Enum, StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory


def _none_like(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "none", "null", "n/a"}
    return False


def _coerce_list(value: object) -> list[object]:
    if _none_like(value):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _enum_value_set(enum_cls: type[Enum]) -> set[str]:
    return {str(item.value) for item in enum_cls}


def _normalize_enum_list(value: object, enum_cls: type[Enum]) -> list[object]:
    allowed = _enum_value_set(enum_cls)
    normalized: list[object] = []

    for item in _coerce_list(value):
        if isinstance(item, enum_cls):
            normalized.append(item)
        elif isinstance(item, str) and item.strip() in allowed:
            normalized.append(item.strip())

    return normalized


def _normalize_single_enum_or_default(
    value: object,
    enum_cls: type[Enum],
    default: object,
) -> object:
    if isinstance(value, enum_cls):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        if stripped in _enum_value_set(enum_cls):
            return stripped

    return default


def _normalize_single_enum_or_none(value: object, enum_cls: type[Enum]) -> object:
    if _none_like(value):
        return None

    if isinstance(value, enum_cls):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        if stripped in _enum_value_set(enum_cls):
            return stripped

    return None


def _normalize_evidence(value: object) -> list[str]:
    if _none_like(value):
        return []

    if isinstance(value, list):
        return [item if isinstance(item, str) else str(item) for item in value if item is not None]

    if isinstance(value, tuple | set):
        return [item if isinstance(item, str) else str(item) for item in value if item is not None]

    return [value if isinstance(value, str) else str(value)]


def _normalize_confidence(value: object) -> object:
    if value is None:
        return 0.0

    if isinstance(value, int | float):
        return value

    if isinstance(value, str):
        stripped = value.strip().replace("%", "")
        try:
            number = float(stripped)
        except ValueError:
            return 0.0

        if "%" in value and number > 1:
            return number / 100

        return number

    return value


class IntentVote(BaseModel):

    @field_validator("query_primary", mode="before")
    @classmethod
    def _normalize_query_primary_field(cls, value: object) -> object:
        return _normalize_single_enum_or_default(value, QueryIntentType, QueryIntentType.UNCLEAR)

    @field_validator("service_primary", mode="before")
    @classmethod
    def _normalize_service_primary_field(cls, value: object) -> object:
        return _normalize_single_enum_or_none(value, ServiceCategory)

    @field_validator("funnel_stage", mode="before")
    @classmethod
    def _normalize_funnel_stage_field(cls, value: object) -> object:
        return _normalize_single_enum_or_default(value, SalesStage, SalesStage.NEW)

    @field_validator("query_secondary", mode="before")
    @classmethod
    def _normalize_query_secondary_field(cls, value: object) -> list[object]:
        return _normalize_enum_list(value, QueryIntentType)

    @field_validator("service_secondary", mode="before")
    @classmethod
    def _normalize_service_secondary_field(cls, value: object) -> list[object]:
        return _normalize_enum_list(value, ServiceCategory)

    @field_validator("evidence", mode="before")
    @classmethod
    def _normalize_evidence_field(cls, value: object) -> list[str]:
        return _normalize_evidence(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence_field(cls, value: object) -> object:
        return _normalize_confidence(value)

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
