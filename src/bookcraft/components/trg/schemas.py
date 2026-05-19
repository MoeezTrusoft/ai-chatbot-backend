from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
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

    # Semantic memory (Phase 8) — stored persistently alongside the graph.
    semantic_facts: list[TRGFactNode] = Field(default_factory=list)
    answered_questions: list[AnsweredQuestion] = Field(default_factory=list)
    contradiction_events: list[ContradictionEvent] = Field(default_factory=list)
    service_shifts: list[ServiceShiftEvent] = Field(default_factory=list)


class GraphUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: TemporalRelationGraph
    added_nodes: list[GraphNode] = Field(default_factory=list)
    added_edges: list[GraphEdge] = Field(default_factory=list)
    unresolved_question_count: int = 0
    contradiction_count: int = 0
    repetition_signal: RepetitionSignal | None = None


# ---------------------------------------------------------------------------
# Semantic memory models (Phase 8)
# ---------------------------------------------------------------------------


class TRGFactNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_path: str
    value: str | int | float | bool
    source_turn_id: str | None = None
    raw_excerpt: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    active: bool = True
    superseded_by: str | None = None


class AnsweredQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: str
    answer_text: str
    fact_path: str | None = None
    resolved: bool = True
    source_turn_id: str | None = None


class ContradictionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_path: str
    old_value: str
    new_value: str
    source_turn_id: str | None = None
    resolution_status: str = "unresolved"


class ServiceShiftEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_service: str | None = None
    new_service: str | None = None
    mode: Literal["switch", "addition", "negation", "inertia"]
    source_turn_id: str | None = None


# ---------------------------------------------------------------------------
# Existing + extended models
# ---------------------------------------------------------------------------


class TRGContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outstanding_questions: list[str] = Field(default_factory=list)
    contradiction_count: int = 0
    repeated_user_messages: list[str] = Field(default_factory=list)
    recent_node_labels: list[str] = Field(default_factory=list)
    compliance_score: float = Field(default=1.0, ge=0.0, le=1.0)

    # Semantic memory fields (Phase 8).
    active_facts: list[TRGFactNode] = Field(default_factory=list)
    answered_questions: list[AnsweredQuestion] = Field(default_factory=list)
    forbidden_reasks: list[str] = Field(default_factory=list)
    contradictions: list[ContradictionEvent] = Field(default_factory=list)
    service_shifts: list[ServiceShiftEvent] = Field(default_factory=list)
