from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class GraphNodeType(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    STATE_FACT = "state_fact"
    QUESTION = "question"


class RelationType(StrEnum):
    MENTIONS = "mentions"
    ASKS = "asks"
    ANSWERS = "answers"
    CONTRADICTS = "contradicts"
    REPEATS = "repeats"
    FOLLOWS = "follows"


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    node_type: GraphNodeType
    label: str
    text: str | None = None
    turn_sequence: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    relation_type: RelationType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    compliance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UnresolvedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    question: str
    asked_turn_sequence: int
    resolved: bool = False
    resolved_turn_sequence: int | None = None


class RepetitionSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_text: str
    count: int
    repeated: bool


class TemporalRelationGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    unresolved_questions: list[UnresolvedQuestion] = Field(default_factory=list)
    repetition_counters: dict[str, int] = Field(default_factory=dict)
    compliance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GraphUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: TemporalRelationGraph
    added_nodes: list[GraphNode] = Field(default_factory=list)
    added_edges: list[GraphEdge] = Field(default_factory=list)
    unresolved_question_count: int = 0
    contradiction_count: int = 0
    repetition_signal: RepetitionSignal | None = None


class TRGContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outstanding_questions: list[str] = Field(default_factory=list)
    contradiction_count: int = 0
    repeated_user_messages: list[str] = Field(default_factory=list)
    recent_node_labels: list[str] = Field(default_factory=list)
    compliance_score: float = Field(default=1.0, ge=0.0, le=1.0)
