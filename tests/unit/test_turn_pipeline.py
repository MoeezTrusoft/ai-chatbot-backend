"""P4-T2 foundation — TurnContext + staged pipeline runner."""
from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.services.turn import TurnContext, run_pipeline, stage, timed_stage


def _ctx() -> TurnContext:
    return TurnContext(thread_id=uuid4(), message="hello")


class TestTurnContext:
    def test_required_fields_and_defaults(self) -> None:
        ctx = _ctx()
        assert ctx.message == "hello"
        assert ctx.state is None
        assert ctx.bubbles == []
        assert ctx.timings == {}
        assert ctx.total_ms() == 0.0

    def test_total_ms_sums_timings(self) -> None:
        ctx = _ctx()
        ctx.timings["a"] = 1.5
        ctx.timings["b"] = 2.5
        assert ctx.total_ms() == pytest.approx(4.0)


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_runs_stages_in_order(self) -> None:
        order: list[str] = []

        @stage("first")
        def s1(ctx: TurnContext) -> None:
            order.append("first")

        @stage("second")
        def s2(ctx: TurnContext) -> None:
            order.append("second")

        await run_pipeline(_ctx(), [s1, s2])
        assert order == ["first", "second"]

    @pytest.mark.asyncio
    async def test_records_timing_per_stage(self) -> None:
        @stage("classify")
        def classify(ctx: TurnContext) -> None:
            ctx.intent = "ghostwriting"

        ctx = await run_pipeline(_ctx(), [classify])
        assert "classify" in ctx.timings
        assert ctx.timings["classify"] >= 0.0
        assert ctx.intent == "ghostwriting"

    @pytest.mark.asyncio
    async def test_async_stage_supported(self) -> None:
        @stage("generate")
        async def generate(ctx: TurnContext) -> None:
            ctx.response_text = "drafted"

        ctx = await run_pipeline(_ctx(), [generate])
        assert ctx.response_text == "drafted"
        assert "generate" in ctx.timings

    @pytest.mark.asyncio
    async def test_stage_can_return_new_context(self) -> None:
        replacement = _ctx()
        replacement.metadata["replaced"] = True

        @stage("swap")
        def swap(ctx: TurnContext) -> TurnContext:
            return replacement

        ctx = await run_pipeline(_ctx(), [swap])
        assert ctx.metadata.get("replaced") is True

    @pytest.mark.asyncio
    async def test_mutation_in_place_when_returning_none(self) -> None:
        @stage("mutate")
        def mutate(ctx: TurnContext) -> None:
            ctx.blocked = True

        ctx = await run_pipeline(_ctx(), [mutate])
        assert ctx.blocked is True

    @pytest.mark.asyncio
    async def test_stage_name_falls_back_to_func_name(self) -> None:
        def safety(ctx: TurnContext) -> None:  # no @stage decorator
            ctx.metadata["safety_ran"] = True

        ctx = await run_pipeline(_ctx(), [safety])
        assert "safety" in ctx.timings
        assert ctx.metadata["safety_ran"] is True

    @pytest.mark.asyncio
    async def test_empty_pipeline_is_noop(self) -> None:
        ctx = await run_pipeline(_ctx(), [])
        assert ctx.timings == {}


class TestTimedStage:
    def test_timed_stage_records_into_ctx(self) -> None:
        ctx = _ctx()
        with timed_stage(ctx, "manual"):
            pass
        assert "manual" in ctx.timings

    def test_timed_stage_records_even_on_exception(self) -> None:
        ctx = _ctx()
        with pytest.raises(ValueError):
            with timed_stage(ctx, "boom"):
                raise ValueError("x")
        assert "boom" in ctx.timings

    def test_timed_stage_without_metric(self) -> None:
        ctx = _ctx()
        with timed_stage(ctx, "nometric", record_metric=False):
            pass
        assert "nometric" in ctx.timings
