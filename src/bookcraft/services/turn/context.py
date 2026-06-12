"""TurnContext — the carrier object for a staged turn (P4-T2).

A single mutable record threaded through the pipeline stages so each stage is a
pure ``(ctx) -> ctx`` function that can be unit-tested and timed in isolation,
rather than reading and writing locals inside a 4,000-line method. Fields are
populated progressively as stages run; types are intentionally loose (``Any``)
so this foundation module stays free of heavy/cyclic imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class TurnContext:
    # ── Inputs (set at construction) ────────────────────────────────────────
    thread_id: UUID
    message: str
    correlation_id: str | None = None
    customer_id: UUID | None = None

    # ── Progressive pipeline state (filled by stages) ───────────────────────
    state: Any = None          # ThreadState
    previous_state: Any = None  # ThreadState snapshot before this turn
    processed: Any = None      # ProcessedMessage (tokens, embedding, spans)
    intent: Any = None         # IntentVote
    trimatch: Any = None       # TriMatch classification
    extraction: Any = None     # CombinedExtraction
    trg_context: Any = None    # TRGContext
    context_pack: Any = None
    plan: Any = None           # ResponsePlan
    response_text: str | None = None

    # ── Outputs / side-channels ─────────────────────────────────────────────
    bubbles: list[Any] = field(default_factory=list)
    events: list[Any] = field(default_factory=list)
    action_events: list[dict[str, Any]] = field(default_factory=list)
    blocked: bool = False

    # ── Instrumentation ─────────────────────────────────────────────────────
    timings: dict[str, float] = field(default_factory=dict)  # stage → milliseconds
    metadata: dict[str, Any] = field(default_factory=dict)

    def total_ms(self) -> float:
        """Sum of all recorded stage timings (ms)."""
        return sum(self.timings.values())
