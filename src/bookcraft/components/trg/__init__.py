"""Temporal Relational Graph component — Phase 8: semantic memory upgrade."""

from .engine import (
    TemporalRelationGraphEngine,
    detect_fact_contradictions,
    detect_service_shift,
    forbidden_reasks_from_facts,
    semantic_facts_from_deltas,
)
from .repository import GraphRepository, InMemoryGraphRepository, RedisHotGraphStore
from .schemas import (
    AnsweredQuestion,
    ContradictionEvent,
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphUpdateResult,
    RelationType,
    RepetitionSignal,
    ServiceShiftEvent,
    TemporalRelationGraph,
    TRGContext,
    TRGFactNode,
    UnresolvedQuestion,
)
from .worker import TRGUpdateWorker

__all__ = [
    "AnsweredQuestion",
    "ContradictionEvent",
    "detect_fact_contradictions",
    "detect_service_shift",
    "forbidden_reasks_from_facts",
    "GraphEdge",
    "GraphNode",
    "GraphNodeType",
    "GraphRepository",
    "GraphUpdateResult",
    "InMemoryGraphRepository",
    "RedisHotGraphStore",
    "RelationType",
    "RepetitionSignal",
    "ServiceShiftEvent",
    "semantic_facts_from_deltas",
    "TRGContext",
    "TRGFactNode",
    "TRGUpdateWorker",
    "TemporalRelationGraph",
    "TemporalRelationGraphEngine",
    "UnresolvedQuestion",
]
