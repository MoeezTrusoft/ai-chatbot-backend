from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from prometheus_client import Counter, Histogram

from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.domain.enums import Source
from bookcraft.domain.state import ThreadState

from .repository import GraphRepository, InMemoryGraphRepository
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

TRG_UPDATES_TOTAL = Counter("trg_updates_total", "TRG updates applied.", ["result"])
TRG_UPDATE_LATENCY = Histogram("trg_update_latency_seconds", "TRG update latency.")

# Mapping from fact_path → list of question phrasings that must not be re-asked
# after the fact has been captured.  Extend this table whenever a new extractable
# fact path is added to the schema.
_REASK_PROTECTION: dict[str, list[str]] = {
    "project.genre": ["genre", "what genre"],
    "project.manuscript_status": ["manuscript_stage", "draft status", "starting from scratch"],
    "project.word_count": ["word count", "how many words", "length of your manuscript"],
    "project.title": ["title", "book title", "name of your book"],
    "project.formats": ["format", "book format", "paperback or ebook"],
    "contact.name": ["your name", "what's your name", "may i have your name"],
    "contact.email": ["email", "email address", "your email"],
    "contact.phone": ["phone", "phone number", "contact number"],
    "service.timeline": ["timeline", "when do you need", "deadline"],
    "service.budget": ["budget", "how much are you looking to spend"],
    "project.page_count": ["page count", "how many pages"],
    "project.platforms": ["platform", "publishing platform", "where will you publish"],
}

# Normalized single-phrase user inputs that carry no real answer content.
# These phrases must NOT be treated as resolving an outstanding question.
_NON_ANSWER_PHRASES: frozenset[str] = frozenset({
    "hi", "hello", "hey", "ok", "okay", "sure", "thanks", "thank you",
    "yes", "no", "yep", "nope", "got it", "alright",
})


def _derive_slot_path(question_text: str) -> str | None:
    """Map an outstanding question to the state path it asks for (P2-T1).

    Reuses the ``_REASK_PROTECTION`` phrasing table so a question and its answer
    slot share one source of truth: if any registered phrasing for a path appears
    in the question, that path is the question's slot. First match wins.
    """
    lowered = question_text.casefold()
    for path, phrasings in _REASK_PROTECTION.items():
        if any(phrase in lowered for phrase in phrasings):
            return path
    return None


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; returns 0.0 for empty or zero-norm vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass(slots=True)
class TemporalRelationGraphEngine:
    repository: GraphRepository = field(default_factory=InMemoryGraphRepository)
    compact_keep: int = 24
    # P2-T1: resolve outstanding questions by slot/embedding match instead of
    # blindly resolving the first one on any substantive message.
    question_matching_enabled: bool = False
    answer_match_threshold: float = 0.6
    # P2-T7: link a repeated message to its prior occurrence with a REPEATS edge.
    repetition_edges_v2: bool = False

    async def update_after_turn(
        self,
        *,
        thread_id: UUID,
        turn_sequence: int,
        user_text: str,
        assistant_text: str,
        previous_state: ThreadState | None = None,
        state_deltas: Iterable[StateDelta] = (),
        arbiter_signals: list[str] | None = None,
        preloaded_graph: TemporalRelationGraph | None = None,
        user_embedding: list[float] | None = None,
    ) -> GraphUpdateResult:
        with TRG_UPDATE_LATENCY.time():
            # Materialize once so helpers can iterate multiple times.
            delta_list = list(state_deltas)
            graph = preloaded_graph if preloaded_graph is not None else await self.repository.load(thread_id)
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
                state_deltas=delta_list,
                user_embedding=user_embedding,
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
                state_deltas=delta_list,
            )
            added_edges.extend(contradiction_edges)

            repetition_signal, repetition_edge = self._track_repetition(graph, user_node, user_text)
            # P2-T7: when enabled, a repeat links to its PRIOR occurrence's node
            # (a queryable REPEATS edge) instead of the old self-edge.  Self-edges
            # remain omitted; the repetition_signal still surfaces in the result.
            if repetition_edge is not None:
                added_edges.append(repetition_edge)

            # Compute engagement weight for this user turn and store on the node.
            user_node.engagement_weight = _compute_engagement_weight(user_text)

            # Phase 8: semantic memory updates.
            turn_id = str(user_node.id)
            _update_semantic_facts(graph, delta_list, turn_id=turn_id)
            _update_answered_questions(
                graph,
                graph.unresolved_questions,
                user_text,
                turn_id=turn_id,
                state_deltas=delta_list,
            )
            _update_service_shifts(graph, arbiter_signals or [], turn_id=turn_id)

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
        repeated = [text for text, count in graph.repetition_counters.items() if count > 1]
        contradictions = sum(
            1 for edge in graph.edges if edge.relation_type == RelationType.CONTRADICTS
        )
        active_facts = [f for f in graph.semantic_facts if f.active]
        forbidden = _forbidden_reasks_from_facts(active_facts)
        return TRGContext(
            outstanding_questions=outstanding,
            contradiction_count=contradictions,
            repeated_user_messages=repeated,
            recent_node_labels=[node.label for node in graph.nodes[-8:]],
            compliance_score=graph.compliance_score,
            # Semantic fields.
            active_facts=active_facts,
            answered_questions=list(graph.answered_questions),
            forbidden_reasks=forbidden,
            contradictions=list(graph.contradiction_events),
            service_shifts=list(graph.service_shifts),
            questions_ignored=sum(
                q.ignored_count for q in graph.unresolved_questions if not q.resolved
            ),
        )

    def compact(self, graph: TemporalRelationGraph) -> None:
        if len(graph.nodes) <= self.compact_keep:
            return

        # STATE_FACT nodes are always retained — they carry durable extracted facts.
        fact_nodes = [n for n in graph.nodes if n.node_type == GraphNodeType.STATE_FACT]
        non_fact_nodes = [n for n in graph.nodes if n.node_type != GraphNodeType.STATE_FACT]

        # Among non-fact nodes, score by recency × engagement_weight.
        # Higher-scored nodes survive; lowest-scored are dropped first.
        total = len(graph.nodes)
        slots_for_non_fact = max(0, self.compact_keep - len(fact_nodes))
        if len(non_fact_nodes) > slots_for_non_fact:
            scored = sorted(
                enumerate(non_fact_nodes),
                # Additive blend: 60% recency + 40% normalised engagement weight.
                # This ensures index-0 nodes with high engagement are NOT silently
                # zeroed out (the old multiplicative formula gave index-0 a score of
                # 0 regardless of engagement_weight).
                key=lambda iv: (
                    0.6 * (iv[0] / max(len(non_fact_nodes) - 1, 1))
                    + 0.4 * (iv[1].engagement_weight / 3.0)
                ),
                reverse=True,
            )
            non_fact_nodes = [n for _, n in scored[:slots_for_non_fact]]

        # Always keep nodes anchoring unresolved questions.
        unresolved_ids = {
            q.node_id for q in graph.unresolved_questions if not q.resolved
        }
        for n in graph.nodes:
            if n.id in unresolved_ids and n not in non_fact_nodes and n not in fact_nodes:
                non_fact_nodes.append(n)

        keep_nodes = fact_nodes + non_fact_nodes
        keep_ids = {n.id for n in keep_nodes}
        graph.nodes = [n for n in graph.nodes if n.id in keep_ids]
        graph.edges = [
            edge
            for edge in graph.edges
            if edge.source_node_id in keep_ids and edge.target_node_id in keep_ids
        ]
        _ = total  # suppress unused-variable lint

        # Prune repetition_counters to prevent unbounded growth.
        # Keep all entries seen more than once (they carry useful repetition signal)
        # plus the N most-recent singletons.
        MAX_SINGLETON_COUNTERS = 50
        multi_seen = {k: v for k, v in graph.repetition_counters.items() if v > 1}
        singletons = {k: v for k, v in graph.repetition_counters.items() if v == 1}
        singleton_items = list(singletons.items())[-MAX_SINGLETON_COUNTERS:]
        graph.repetition_counters = {**multi_seen, **dict(singleton_items)}

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
                    slot_path=_derive_slot_path(question),
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
        state_deltas: Iterable[StateDelta] = (),
        user_embedding: list[float] | None = None,
    ) -> list[GraphEdge]:
        stripped = user_text.strip()
        if not stripped:
            return []

        # A one-word greeting / acknowledgment is not a meaningful answer.
        normalized_input = stripped.casefold()
        if normalized_input in _NON_ANSWER_PHRASES:
            return []

        # Fewer than 3 words is almost certainly not a real answer to a question.
        if len(stripped.split()) < 3:  # noqa: PLR2004
            return []

        if self.question_matching_enabled:
            return self._resolve_questions_by_match(
                graph,
                answer_node=answer_node,
                user_text=user_text,
                turn_sequence=turn_sequence,
                state_deltas=state_deltas,
                user_embedding=user_embedding,
            )

        # Legacy behavior: resolve the first outstanding question (flag off).
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

    def _resolve_questions_by_match(
        self,
        graph: TemporalRelationGraph,
        *,
        answer_node: GraphNode,
        user_text: str,
        turn_sequence: int,
        state_deltas: Iterable[StateDelta],
        user_embedding: list[float] | None,
    ) -> list[GraphEdge]:
        """P2-T1: resolve every question this message actually answers.

        A question resolves when EITHER a state delta this turn writes the
        question's ``slot_path``, OR the message embedding is similar enough to the
        question's embedding (``answer_match_threshold``).  Questions matching
        neither stay open and have their ``ignored_count`` incremented — a
        dodge/avoidance sales signal exposed via ``TRGContext.questions_ignored``.
        """
        delta_paths = {d.path for d in state_deltas}
        edges: list[GraphEdge] = []
        for question in graph.unresolved_questions:
            if question.resolved:
                continue
            matched_by_slot = bool(question.slot_path and question.slot_path in delta_paths)
            matched_by_embedding = bool(
                question.embedding
                and user_embedding
                and _cosine(question.embedding, user_embedding) >= self.answer_match_threshold
            )
            if matched_by_slot or matched_by_embedding:
                question.resolved = True
                question.resolved_turn_sequence = turn_sequence
                edges.append(
                    self._add_edge(
                        graph,
                        answer_node,
                        question.node_id,
                        RelationType.ANSWERS,
                        confidence=0.85 if matched_by_slot else 0.7,
                        evidence=user_text[:240],
                    )
                )
            else:
                question.ignored_count += 1
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
    ) -> tuple[RepetitionSignal, GraphEdge | None]:
        normalized = normalize_text(user_text)
        if not normalized:
            return RepetitionSignal(normalized_text="", count=0, repeated=False), None
        graph.repetition_counters[normalized] = graph.repetition_counters.get(normalized, 0) + 1
        count = graph.repetition_counters[normalized]
        user_node.metadata["repetition_count"] = count

        edge: GraphEdge | None = None
        if self.repetition_edges_v2:
            prior_id = graph.repetition_first_node_id.get(normalized)
            if prior_id is None:
                # First occurrence: remember this node as the repetition anchor.
                graph.repetition_first_node_id[normalized] = user_node.id
            elif prior_id != user_node.id and any(n.id == prior_id for n in graph.nodes):
                # Repeat: link to the prior occurrence (if it still exists post-compaction).
                edge = self._add_edge(
                    graph,
                    user_node,
                    prior_id,
                    RelationType.REPEATS,
                    evidence=f"repeat #{count} of: {normalized[:120]}",
                )
        return RepetitionSignal(normalized_text=normalized, count=count, repeated=count > 1), edge

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


# ---------------------------------------------------------------------------
# Phase 8: Public semantic-memory helpers
# ---------------------------------------------------------------------------


def semantic_facts_from_deltas(state_deltas: Iterable[StateDelta]) -> list[TRGFactNode]:
    """Convert state deltas to TRGFactNode objects (pure, no graph mutation)."""
    facts: list[TRGFactNode] = []
    for delta in state_deltas:
        raw_value = delta.value
        if not isinstance(raw_value, str | int | float | bool):
            raw_value = str(raw_value)
        facts.append(
            TRGFactNode(
                fact_path=delta.path,
                value=raw_value,
                raw_excerpt=delta.raw_excerpt,
                confidence=delta.confidence,
                active=True,
                source_extraction=delta.source == Source.AI_EXTRACTED,
            )
        )
    return facts


# Correction-phrase signals that raise engagement weight.
_CORRECTION_KEYWORDS = frozenset([
    "actually", "correction", "i meant", "not that", "wait no", "i was wrong",
    "let me correct", "change it to", "i decided", "now it's",
])


def _compute_engagement_weight(user_text: str) -> float:
    """Score a user turn by how much it engages with the conversation.

    Higher score = retain longer under compaction pressure.
    Range: 1.0 (plain statement) → 3.0 (high-engagement correction + questions).
    """
    text_lower = user_text.lower()
    weight = 1.0

    # Questions asked by the user (genuine information-seeking)
    question_count = user_text.count("?")
    if question_count >= 2:  # noqa: PLR2004
        weight += 1.0
    elif question_count == 1:
        weight += 0.5

    # Explicit correction signals raise stakes — this turn overrides prior facts
    if any(kw in text_lower for kw in _CORRECTION_KEYWORDS):
        weight += 1.0

    # Long messages tend to carry more information
    word_count = len(user_text.split())
    if word_count > 60:  # noqa: PLR2004
        weight += 0.5

    return min(3.0, weight)


def forbidden_reasks_from_facts(active_facts: list[TRGFactNode]) -> list[str]:
    """Return labels that must not be asked again given the supplied active facts.

    Uses the module-level ``_REASK_PROTECTION`` mapping so every registered
    fact path is automatically covered — not just genre and manuscript_status.
    """
    forbidden: list[str] = []
    for fact in active_facts:
        phrasings = _REASK_PROTECTION.get(fact.fact_path)
        if phrasings:
            forbidden.extend(phrasings)
    seen: set[str] = set()
    result: list[str] = []
    for item in forbidden:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def detect_fact_contradictions(
    existing_facts: list[TRGFactNode],
    incoming_facts: list[TRGFactNode],
) -> list[ContradictionEvent]:
    """Return ContradictionEvents where incoming facts differ from current active facts."""
    existing_by_path = {f.fact_path: f for f in existing_facts if f.active}
    events: list[ContradictionEvent] = []
    for incoming in incoming_facts:
        existing = existing_by_path.get(incoming.fact_path)
        if existing is None:
            continue
        if str(existing.value).casefold() != str(incoming.value).casefold():
            events.append(
                ContradictionEvent(
                    fact_path=incoming.fact_path,
                    old_value=str(existing.value),
                    new_value=str(incoming.value),
                    resolution_status="unresolved",
                )
            )
    return events


def detect_service_shift(
    previous_service: str | None,
    new_service: str | None,
    arbiter_signals: list[str],
) -> ServiceShiftEvent | None:
    """Return the first ServiceShiftEvent detectable from the given arbiter signals."""
    for signal in arbiter_signals:
        if signal.startswith("state_service_inertia:"):
            parts = signal[len("state_service_inertia:") :].split("→", 1)
            prev = (parts[0] or None) if parts else None
            nxt = (parts[1] or None) if len(parts) > 1 else None
            return ServiceShiftEvent(
                previous_service=prev or previous_service,
                new_service=nxt or new_service,
                mode="inertia",
            )
        if signal == "explicit_service_switch":
            return ServiceShiftEvent(
                previous_service=previous_service,
                new_service=new_service,
                mode="switch",
            )
        if signal.startswith("additive_service:"):
            raw = signal[len("additive_service:") :].split("→")[0]
            return ServiceShiftEvent(
                previous_service=previous_service,
                new_service=raw or new_service,
                mode="addition",
            )
    return None


# ---------------------------------------------------------------------------
# Private graph-mutation helpers (delegate to public helpers above)
# ---------------------------------------------------------------------------


def _update_semantic_facts(
    graph: TemporalRelationGraph,
    state_deltas: Iterable[StateDelta],
    *,
    turn_id: str,
) -> None:
    incoming = semantic_facts_from_deltas(state_deltas)
    if not incoming:
        return

    existing_active = [f for f in graph.semantic_facts if f.active]

    # Record contradictions using the pure public helper.
    for evt in detect_fact_contradictions(existing_active, incoming):
        graph.contradiction_events.append(evt.model_copy(update={"source_turn_id": turn_id}))

    # Supersede any existing active fact whose path is covered by an incoming fact.
    incoming_paths = {f.fact_path for f in incoming}
    for existing in graph.semantic_facts:
        if existing.active and existing.fact_path in incoming_paths:
            existing.active = False
            existing.superseded_by = turn_id

    # Append new facts, stamping the current turn.
    for fact in incoming:
        graph.semantic_facts.append(fact.model_copy(update={"source_turn_id": turn_id}))


def _update_answered_questions(
    graph: TemporalRelationGraph,
    unresolved_questions: list[UnresolvedQuestion],
    user_text: str,
    *,
    turn_id: str,
    state_deltas: list[StateDelta] | None = None,
) -> None:
    if not user_text.strip():
        return
    # Use the first state delta's path as fact_path for the answered question.
    # This links the user's answer to the fact it updated (e.g. project.manuscript_status).
    inferred_fact_path: str | None = state_deltas[0].path if state_deltas else None
    for question in unresolved_questions:
        if question.resolved and question.resolved_turn_sequence is not None:
            # Already resolved in this same call; create AnsweredQuestion record.
            already_recorded = any(
                aq.question_text == question.question for aq in graph.answered_questions
            )
            if not already_recorded:
                graph.answered_questions.append(
                    AnsweredQuestion(
                        question_text=question.question,
                        answer_text=user_text[:240],
                        fact_path=inferred_fact_path,
                        resolved=True,
                        source_turn_id=turn_id,
                    )
                )


def _update_service_shifts(
    graph: TemporalRelationGraph,
    arbiter_signals: list[str],
    *,
    turn_id: str,
) -> None:
    """Graph-mutation wrapper: applies detect_service_shift for every signal."""
    for signal in arbiter_signals:
        shift = detect_service_shift(None, None, [signal])
        if shift is not None:
            graph.service_shifts.append(shift.model_copy(update={"source_turn_id": turn_id}))


# Backward-compat alias so existing test imports continue to work.
_forbidden_reasks_from_facts = forbidden_reasks_from_facts


def extract_questions(text: str) -> list[str]:
    """Return the question clause(s) from *text*, stripping any leading statements.

    Example: "Here is the plan. What is your budget?" → ["What is your budget?"]
    """
    questions = re.findall(r"([^?]{3,}\?)", text)
    cleaned: list[str] = []
    for q in questions:
        # Split on sentence-ending punctuation and take only the final clause
        # so that leading declarative statements are discarded.
        sentences = re.split(r"(?<=[.!])\s+", q.strip())
        last = sentences[-1].strip() if sentences else q.strip()
        if len(last) >= 3:  # noqa: PLR2004
            cleaned.append(" ".join(last.split()))
    return cleaned


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def get_state_value(state: ThreadState, path: str) -> object | None:
    owner_name, field_name = path.split(".", 1)
    owner = getattr(state, owner_name)
    field = getattr(owner, field_name)
    return getattr(field, "value", None)
