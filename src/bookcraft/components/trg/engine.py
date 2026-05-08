from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from prometheus_client import Counter, Histogram

from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.domain.state import ThreadState

from .repository import GraphRepository, InMemoryGraphRepository
from .schemas import (
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphUpdateResult,
    RelationType,
    RepetitionSignal,
    TemporalRelationGraph,
    TRGContext,
    UnresolvedQuestion,
)

TRG_UPDATES_TOTAL = Counter("trg_updates_total", "TRG updates applied.", ["result"])
TRG_UPDATE_LATENCY = Histogram("trg_update_latency_seconds", "TRG update latency.")


@dataclass(slots=True)
class TemporalRelationGraphEngine:
    repository: GraphRepository = field(default_factory=InMemoryGraphRepository)
    compact_keep: int = 24

    async def update_after_turn(
        self,
        *,
        thread_id: UUID,
        turn_sequence: int,
        user_text: str,
        assistant_text: str,
        previous_state: ThreadState | None = None,
        state_deltas: Iterable[StateDelta] = (),
    ) -> GraphUpdateResult:
        with TRG_UPDATE_LATENCY.time():
            graph = await self.repository.load(thread_id)
            if graph is None:
                graph = TemporalRelationGraph(thread_id=thread_id)

            added_nodes: list[GraphNode] = []
            added_edges: list[GraphEdge] = []
            user_node = self._add_node(
                graph,
                GraphNodeType.USER_MESSAGE,
                "User message",
                user_text,
                turn_sequence,
            )
            assistant_node = self._add_node(
                graph,
                GraphNodeType.ASSISTANT_MESSAGE,
                "Assistant response",
                assistant_text,
                turn_sequence,
            )
            added_nodes.extend([user_node, assistant_node])
            added_edges.append(
                self._add_edge(
                    graph,
                    user_node,
                    assistant_node,
                    RelationType.FOLLOWS,
                    evidence="assistant response follows user message",
                )
            )

            resolved_edges = self._resolve_outstanding_questions(
                graph,
                answer_node=user_node,
                user_text=user_text,
                turn_sequence=turn_sequence,
            )
            added_edges.extend(resolved_edges)
            question_nodes, question_edges = self._track_assistant_questions(
                graph,
                assistant_node=assistant_node,
                assistant_text=assistant_text,
                turn_sequence=turn_sequence,
            )
            added_nodes.extend(question_nodes)
            added_edges.extend(question_edges)

            contradiction_edges = self._track_contradictions(
                graph,
                user_node=user_node,
                previous_state=previous_state,
                state_deltas=state_deltas,
            )
            added_edges.extend(contradiction_edges)

            repetition_signal = self._track_repetition(graph, user_node, user_text)
            if repetition_signal.repeated:
                added_edges.append(
                    self._add_edge(
                        graph,
                        user_node,
                        user_node,
                        RelationType.REPEATS,
                        confidence=0.95,
                        evidence="normalized user message repeated",
                    )
                )

            graph.updated_at = datetime.now(UTC)
            self.compact(graph)
            await self.repository.save(graph)
            TRG_UPDATES_TOTAL.labels(result="applied").inc()
            return GraphUpdateResult(
                graph=graph,
                added_nodes=added_nodes,
                added_edges=added_edges,
                unresolved_question_count=sum(
                    1 for question in graph.unresolved_questions if not question.resolved
                ),
                contradiction_count=len(contradiction_edges),
                repetition_signal=repetition_signal,
            )

    def build_context(self, graph: TemporalRelationGraph) -> TRGContext:
        outstanding = [
            question.question for question in graph.unresolved_questions if not question.resolved
        ]
        repeated = [
            text for text, count in graph.repetition_counters.items() if count > 1
        ]
        contradictions = sum(
            1 for edge in graph.edges if edge.relation_type == RelationType.CONTRADICTS
        )
        return TRGContext(
            outstanding_questions=outstanding,
            contradiction_count=contradictions,
            repeated_user_messages=repeated,
            recent_node_labels=[node.label for node in graph.nodes[-8:]],
            compliance_score=graph.compliance_score,
        )

    def compact(self, graph: TemporalRelationGraph) -> None:
        if len(graph.nodes) <= self.compact_keep:
            return
        keep_nodes = graph.nodes[-self.compact_keep :]
        keep_ids = {node.id for node in keep_nodes}
        keep_ids.update(
            question.node_id for question in graph.unresolved_questions if not question.resolved
        )
        graph.nodes = [node for node in graph.nodes if node.id in keep_ids]
        graph.edges = [
            edge
            for edge in graph.edges
            if edge.source_node_id in keep_ids and edge.target_node_id in keep_ids
        ]

    def _track_assistant_questions(
        self,
        graph: TemporalRelationGraph,
        *,
        assistant_node: GraphNode,
        assistant_text: str,
        turn_sequence: int,
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        for question in extract_questions(assistant_text):
            question_node = self._add_node(
                graph,
                GraphNodeType.QUESTION,
                "Outstanding question",
                question,
                turn_sequence,
            )
            graph.unresolved_questions.append(
                UnresolvedQuestion(
                    node_id=question_node.id,
                    question=question,
                    asked_turn_sequence=turn_sequence,
                )
            )
            nodes.append(question_node)
            edges.append(
                self._add_edge(
                    graph,
                    assistant_node,
                    question_node,
                    RelationType.ASKS,
                    evidence=question,
                )
            )
        return nodes, edges

    def _resolve_outstanding_questions(
        self,
        graph: TemporalRelationGraph,
        *,
        answer_node: GraphNode,
        user_text: str,
        turn_sequence: int,
    ) -> list[GraphEdge]:
        if not user_text.strip():
            return []
        edges: list[GraphEdge] = []
        for question in graph.unresolved_questions:
            if question.resolved:
                continue
            question.resolved = True
            question.resolved_turn_sequence = turn_sequence
            edges.append(
                self._add_edge(
                    graph,
                    answer_node,
                    question.node_id,
                    RelationType.ANSWERS,
                    confidence=0.75,
                    evidence=user_text[:240],
                )
            )
            break
        return edges

    def _track_contradictions(
        self,
        graph: TemporalRelationGraph,
        *,
        user_node: GraphNode,
        previous_state: ThreadState | None,
        state_deltas: Iterable[StateDelta],
    ) -> list[GraphEdge]:
        if previous_state is None:
            return []
        edges: list[GraphEdge] = []
        for delta in state_deltas:
            previous_value = get_state_value(previous_state, delta.path)
            previous_normalized = str(previous_value).casefold()
            incoming_normalized = str(delta.value).casefold()
            if previous_value is None or previous_normalized == incoming_normalized:
                continue
            fact_node = self._add_node(
                graph,
                GraphNodeType.STATE_FACT,
                delta.path,
                str(previous_value),
                user_node.turn_sequence,
                metadata={"new_value": delta.value},
            )
            edges.append(
                self._add_edge(
                    graph,
                    user_node,
                    fact_node,
                    RelationType.CONTRADICTS,
                    confidence=min(1.0, max(0.0, delta.confidence)),
                    compliance_score=0.6,
                    evidence=delta.raw_excerpt,
                )
            )
        return edges

    def _track_repetition(
        self,
        graph: TemporalRelationGraph,
        user_node: GraphNode,
        user_text: str,
    ) -> RepetitionSignal:
        normalized = normalize_text(user_text)
        if not normalized:
            return RepetitionSignal(normalized_text="", count=0, repeated=False)
        graph.repetition_counters[normalized] = graph.repetition_counters.get(normalized, 0) + 1
        count = graph.repetition_counters[normalized]
        user_node.metadata["repetition_count"] = count
        return RepetitionSignal(normalized_text=normalized, count=count, repeated=count > 1)

    @staticmethod
    def _add_node(
        graph: TemporalRelationGraph,
        node_type: GraphNodeType,
        label: str,
        text: str | None,
        turn_sequence: int,
        metadata: dict[str, object] | None = None,
    ) -> GraphNode:
        node = GraphNode(
            thread_id=graph.thread_id,
            node_type=node_type,
            label=label,
            text=text,
            turn_sequence=turn_sequence,
            metadata=metadata or {},
        )
        graph.nodes.append(node)
        return node

    @staticmethod
    def _add_edge(
        graph: TemporalRelationGraph,
        source: GraphNode,
        target: GraphNode | UUID,
        relation_type: RelationType,
        *,
        confidence: float = 1.0,
        compliance_score: float = 1.0,
        evidence: str | None = None,
    ) -> GraphEdge:
        target_id = target if isinstance(target, UUID) else target.id
        edge = GraphEdge(
            thread_id=graph.thread_id,
            source_node_id=source.id,
            target_node_id=target_id,
            relation_type=relation_type,
            confidence=confidence,
            compliance_score=compliance_score,
            evidence=evidence,
        )
        graph.edges.append(edge)
        graph.compliance_score = min(graph.compliance_score, compliance_score)
        return edge


def extract_questions(text: str) -> list[str]:
    questions = re.findall(r"([^?]{3,}\?)", text)
    return [" ".join(question.split()) for question in questions]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def get_state_value(state: ThreadState, path: str) -> object | None:
    owner_name, field_name = path.split(".", 1)
    owner = getattr(state, owner_name)
    field = getattr(owner, field_name)
    return getattr(field, "value", None)
