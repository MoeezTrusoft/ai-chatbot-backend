"""Staged turn pipeline (P4-T2 foundation).

This package introduces the structural primitives the plan's P4-T2 calls for —
a ``TurnContext`` carrier and a per-stage, independently-timed pipeline runner —
without rewriting the existing ``ChatService.handle_turn`` monolith in one risky
step. The service↔API import cycle (P4-T2 step 3) is already resolved via
``components/response/chat_schemas``. New stage logic can be migrated onto
``run_pipeline`` incrementally behind ``staged_pipeline_enabled``; until then the
monolith remains the execution path and these primitives are exercised in tests.
"""
from __future__ import annotations

from .context import TurnContext
from .pipeline import Stage, run_pipeline, stage, timed_stage

__all__ = ["TurnContext", "Stage", "run_pipeline", "stage", "timed_stage"]
