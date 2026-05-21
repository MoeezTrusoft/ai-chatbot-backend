from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory


class RagChunkMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: str = "bookcraft_knowledge"
    title: str
    service_category: ServiceCategory | None = None
    subservice: str | None = None
    audience: str | None = None
    funnel_stage: SalesStage | None = None
    section: str
    source_filename: str
    tags: list[str] = Field(default_factory=list)
    content_version: str


class RagChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    content: str
    content_vector: list[float] = Field(default_factory=list)
    metadata: RagChunkMetadata
    checksum: str
    allowed_for_response: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RejectedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_filename: str
    section: str
    reason: str
    pattern: str
    excerpt: str


class RagIngestionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted_count: int
    rejected_count: int
    source_checksums: dict[str, str]
    verifier_status: str
    created_index_name: str | None = None
    rejected_chunks: list[RejectedChunk] = Field(default_factory=list)


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    content: str
    score: float
    service_category: ServiceCategory | None = None
    section: str
    source_id: str
    title: str
    checksum: str
    citation: str


class RagRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_query: str
    query_embedding: list[float]
    query_intent: QueryIntentType | None = None
    service_intent: ServiceCategory | None = None
    top_k: int = 8
