from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    customer_id: UUID | None = None
    turn_sequence: int
    invoked_by: str
    correlation_id: str
    idempotency_key: str
    environment: str


class ToolResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    status: str
    result: dict[str, object] = Field(default_factory=dict)
    replayed: bool = False

