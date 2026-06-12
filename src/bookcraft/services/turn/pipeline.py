"""Stage pipeline runner with per-stage timing (P4-T2).

A ``Stage`` is a callable ``(TurnContext) -> TurnContext | None`` (sync or async).
``run_pipeline`` runs stages in order, timing each into ``ctx.timings`` and the
shared ``STAGE_LATENCY`` histogram (the same metric P0-T1 wraps the monolith's
blocks in), so a migrated pipeline reuses the existing observability for free.
A stage may mutate ``ctx`` in place and return ``None``, or return a new context.
"""
from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Union

from bookcraft.infra.observability import STAGE_LATENCY

from .context import TurnContext

Stage = Callable[[TurnContext], Union[TurnContext, None, Awaitable[Union[TurnContext, None]]]]


def stage(name: str) -> Callable[[Stage], Stage]:
    """Decorator stamping a stable ``stage_name`` used as the timing label."""

    def _decorator(fn: Stage) -> Stage:
        fn.stage_name = name  # type: ignore[attr-defined]
        return fn

    return _decorator


def _stage_name(fn: Stage) -> str:
    return getattr(fn, "stage_name", getattr(fn, "__name__", "stage"))


@contextmanager
def timed_stage(ctx: TurnContext, name: str, *, record_metric: bool = True):
    """Time a block into ``ctx.timings[name]`` (ms) and ``STAGE_LATENCY``."""
    start = time.perf_counter()
    try:
        if record_metric:
            with STAGE_LATENCY.labels(stage=name).time():
                yield
        else:
            yield
    finally:
        ctx.timings[name] = (time.perf_counter() - start) * 1000.0


async def run_pipeline(
    ctx: TurnContext, stages: list[Stage], *, record_metric: bool = True
) -> TurnContext:
    """Run stages in order, timing each. Honors sync and async stages.

    A stage that returns a ``TurnContext`` replaces the running context; one that
    returns ``None`` is assumed to have mutated ``ctx`` in place.
    """
    for fn in stages:
        name = _stage_name(fn)
        with timed_stage(ctx, name, record_metric=record_metric):
            result = fn(ctx)
            if inspect.isawaitable(result):
                result = await result
        if isinstance(result, TurnContext):
            ctx = result
    return ctx
