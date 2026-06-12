"""TRG graph rebuilder — replays the thread event log to reconstruct graph state.

Used in cold-start paths when the Redis hot graph has expired and persisted
semantic facts alone are insufficient to reconstruct full conversation context
(questions, answers, contradictions, service shifts).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from bookcraft.components.trg.engine import TemporalRelationGraphEngine
from bookcraft.components.trg.schemas import TemporalRelationGraph


logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class RebuildResult:
    graph: TemporalRelationGraph
    turns_replayed: int
    errors: list[str] = field(default_factory=list)
    skipped_events: int = 0


async def rebuild_graph(
    *,
    thread_id: UUID,
    engine: TemporalRelationGraphEngine,
    events: list[dict[str, Any]],
) -> RebuildResult:
    """Replay a thread's event log to reconstruct the TRG graph.

    Only `user.message` + `assistant.response` pairs are replayed, grouped
    by turn via consecutive sequence numbers. Extraction deltas embedded in
    `intent.classified` events are also replayed when present.

    Args:
        thread_id: The thread whose graph should be rebuilt.
        engine: A TemporalRelationGraphEngine (repository used to save result).
        events: Ordered list of raw event dicts with keys: sequence, event_type, payload.

    Returns:
        RebuildResult with the reconstructed graph.
    """
    # Group events into turns: each turn = one user.message + one assistant.response
    turns = _extract_turns(events)
    errors: list[str] = []
    skipped = 0
    turns_replayed = 0

    for turn in turns:
        try:
            await engine.update_after_turn(
                thread_id=thread_id,
                turn_sequence=turn["sequence"],
                user_text=turn["user_text"],
                assistant_text=turn["assistant_text"],
                state_deltas=iter(turn.get("state_deltas", [])),
            )
            turns_replayed += 1
        except Exception as exc:
            errors.append(f"turn {turn['sequence']}: {exc.__class__.__name__}: {exc}")
            skipped += 1
            logger.warning(
                "trg_rebuild_turn_failed",
                thread_id=str(thread_id),
                turn_sequence=turn["sequence"],
                exception_class=exc.__class__.__name__,
            )

    graph = await engine.repository.load(thread_id)
    if graph is None:
        graph = TemporalRelationGraph(thread_id=thread_id)

    return RebuildResult(
        graph=graph,
        turns_replayed=turns_replayed,
        errors=errors,
        skipped_events=skipped,
    )


def _extract_turns(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract (user_text, assistant_text, sequence) triples from event log.

    Pairs consecutive user.message → assistant.response events into turns.
    Extra events (trimatch.voted, intent.classified, etc.) are scanned for
    state_deltas to enrich TRG fact tracking.
    """
    turns: list[dict[str, Any]] = []
    pending_user: dict[str, Any] | None = None
    pending_sequence: int = 0
    pending_deltas: list[Any] = []

    for evt in events:
        etype = evt.get("event_type", "")
        payload = evt.get("payload") or {}

        if etype == "user.message":
            pending_user = payload
            pending_sequence = evt.get("sequence", 0)
            pending_deltas = []

        elif etype == "intent.classified":
            # Capture state_deltas if embedded (from extraction event)
            intent_data = payload.get("intent") or {}
            if intent_data.get("state_deltas"):
                pending_deltas.extend(intent_data["state_deltas"])

        elif etype == "assistant.response" and pending_user is not None:
            turns.append({
                "user_text": pending_user.get("text", ""),
                "assistant_text": payload.get("preview", ""),
                "sequence": (pending_sequence + evt.get("sequence", 0)) // 2 or len(turns) + 1,
                "state_deltas": list(pending_deltas),
            })
            pending_user = None
            pending_deltas = []

    return turns
