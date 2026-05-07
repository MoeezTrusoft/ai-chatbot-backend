from __future__ import annotations

from bookcraft.components.pricing.engine import PricingTimelineEngine
from bookcraft.components.pricing.schemas import (
    ExplainQuoteAssumptionsRequest,
    ExplainQuoteAssumptionsResponse,
    PricingQuoteRequest,
    PricingQuoteResponse,
    RequiredInputsRequest,
    RequiredInputsResponse,
    TimelineEstimateRequest,
    TimelineEstimateResponse,
)
from bookcraft.domain.enums import ToolClass
from bookcraft.tools.dispatcher import RetryPolicy, ToolDefinition, ToolRegistry
from bookcraft.tools.schemas import ToolContext


def register_pricing_tools(registry: ToolRegistry, engine: PricingTimelineEngine) -> None:
    async def quote_handler(
        request: PricingQuoteRequest,
        context: ToolContext,
    ) -> PricingQuoteResponse:
        del context
        return engine.quote(request)

    async def timeline_handler(
        request: TimelineEstimateRequest,
        context: ToolContext,
    ) -> TimelineEstimateResponse:
        del context
        return engine.timeline(request)

    async def required_handler(
        request: RequiredInputsRequest,
        context: ToolContext,
    ) -> RequiredInputsResponse:
        del context
        return engine.list_required_inputs(request)

    async def assumptions_handler(
        request: ExplainQuoteAssumptionsRequest,
        context: ToolContext,
    ) -> ExplainQuoteAssumptionsResponse:
        del context
        return ExplainQuoteAssumptionsResponse(
            quote_id=request.quote_id,
            assumptions=request.assumptions,
            explanation="; ".join(request.assumptions) if request.assumptions else "",
        )

    registry.register(
        ToolDefinition(
            name="get_pricing_quote.v1",
            tool_class=ToolClass.WRITE_STATE,
            input_model=PricingQuoteRequest,
            output_model=PricingQuoteResponse,
            handler=quote_handler,
            timeout_seconds=5.0,
            retry_policy=RetryPolicy(attempts=2),
        )
    )
    registry.register(
        ToolDefinition(
            name="get_timeline_estimate.v1",
            tool_class=ToolClass.READ,
            input_model=TimelineEstimateRequest,
            output_model=TimelineEstimateResponse,
            handler=timeline_handler,
            timeout_seconds=5.0,
            retry_policy=RetryPolicy(attempts=2),
        )
    )
    registry.register(
        ToolDefinition(
            name="list_required_quote_inputs.v1",
            tool_class=ToolClass.READ,
            input_model=RequiredInputsRequest,
            output_model=RequiredInputsResponse,
            handler=required_handler,
            timeout_seconds=2.0,
            retry_policy=RetryPolicy(attempts=1),
        )
    )
    registry.register(
        ToolDefinition(
            name="explain_quote_assumptions.v1",
            tool_class=ToolClass.READ,
            input_model=ExplainQuoteAssumptionsRequest,
            output_model=ExplainQuoteAssumptionsResponse,
            handler=assumptions_handler,
            timeout_seconds=2.0,
            retry_policy=RetryPolicy(attempts=1),
        )
    )
