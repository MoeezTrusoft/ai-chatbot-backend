from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from bookcraft.components.pricing.config import ConfigValidationResult, validate_engine_config
from bookcraft.components.pricing.engine import PricingTimelineEngine
from bookcraft.components.pricing.models import (
    PricingQuoteRequest,
    PricingTimelineQuote,
    QuoteStatus,
    ServiceCategory,
)
from bookcraft.components.pricing.schemas import (
    PricingQuoteRequest as LegacyPricingQuoteRequest,
)
from bookcraft.components.pricing.schemas import (
    PricingQuoteResponse as LegacyPricingQuoteResponse,
)
from bookcraft.components.pricing.schemas import (
    RequiredInputsRequest as LegacyRequiredInputsRequest,
)
from bookcraft.components.pricing.schemas import (
    RequiredInputsResponse as LegacyRequiredInputsResponse,
)
from bookcraft.components.pricing.schemas import (
    TimelineEstimateRequest as LegacyTimelineEstimateRequest,
)
from bookcraft.components.pricing.schemas import (
    TimelineEstimateResponse as LegacyTimelineEstimateResponse,
)
from bookcraft.domain.enums import ToolClass
from bookcraft.tools.dispatcher import RetryPolicy, ToolDefinition, ToolRegistry
from bookcraft.tools.schemas import ToolContext


class V2ListRequiredInputsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    services: list[ServiceCategory]


class QuoteAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: UUID
    confirmed_by: str = "user"


class EmptyPricingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def register_pricing_tools(registry: ToolRegistry, engine: PricingTimelineEngine) -> None:
    async def v2_required_handler(
        request: V2ListRequiredInputsRequest,
        context: ToolContext,
    ) -> ConfigValidationResult:
        del context
        data = engine.list_required_inputs(request.services)
        return ConfigValidationResult(
            valid=True,
            warnings=[
                f"{service.value}:{missing.field}:{missing.question}"
                for service, missing_items in data.items()
                for missing in missing_items
            ],
        )

    async def v2_quote_handler(
        request: PricingQuoteRequest,
        context: ToolContext,
    ) -> PricingTimelineQuote:
        del context
        return engine.quote(request)

    async def v2_formal_handler(
        request: PricingQuoteRequest,
        context: ToolContext,
    ) -> PricingTimelineQuote:
        del context
        return engine.quote(request.model_copy(update={"quote_mode": "formal_quote"}))

    async def accept_handler(
        request: QuoteAcceptRequest,
        context: ToolContext,
    ) -> PricingTimelineQuote:
        del context
        return engine.accept_quote(request.quote_id, request.confirmed_by)

    async def config_validate_handler(
        request: EmptyPricingRequest,
        context: ToolContext,
    ) -> ConfigValidationResult:
        del request, context
        return validate_engine_config(engine.config)

    async def legacy_quote_handler(
        request: LegacyPricingQuoteRequest,
        context: ToolContext,
    ) -> LegacyPricingQuoteResponse:
        quote = engine.quote(_legacy_to_v2(request))
        return _legacy_quote_response(request, quote)

    async def legacy_timeline_handler(
        request: LegacyTimelineEstimateRequest,
        context: ToolContext,
    ) -> LegacyTimelineEstimateResponse:
        quote = engine.quote(_legacy_to_v2(request))
        return _legacy_timeline_response(request, quote)

    async def legacy_required_handler(
        request: LegacyRequiredInputsRequest,
        context: ToolContext,
    ) -> LegacyRequiredInputsResponse:
        del context
        missing = engine.list_required_inputs([ServiceCategory(request.service)])
        questions = missing[ServiceCategory(request.service)]
        return LegacyRequiredInputsResponse(
            service=request.service,
            missing_inputs=[item.field for item in questions],
            suggested_question=questions[0].question if questions else "",
        )

    registry.register(
        ToolDefinition(
            name="pricing.list_required_inputs.v2",
            tool_class=ToolClass.READ,
            input_model=V2ListRequiredInputsRequest,
            output_model=ConfigValidationResult,
            handler=v2_required_handler,
            timeout_seconds=2.0,
            retry_policy=RetryPolicy(attempts=1),
        )
    )
    registry.register(
        ToolDefinition(
            name="pricing.quote.estimate.v2",
            tool_class=ToolClass.WRITE_STATE,
            input_model=PricingQuoteRequest,
            output_model=PricingTimelineQuote,
            handler=v2_quote_handler,
            timeout_seconds=5.0,
            retry_policy=RetryPolicy(attempts=2),
        )
    )
    registry.register(
        ToolDefinition(
            name="pricing.quote.formalize.v2",
            tool_class=ToolClass.WRITE_STATE,
            input_model=PricingQuoteRequest,
            output_model=PricingTimelineQuote,
            handler=v2_formal_handler,
            timeout_seconds=5.0,
            retry_policy=RetryPolicy(attempts=2),
        )
    )
    registry.register(
        ToolDefinition(
            name="pricing.quote.accept.v1",
            tool_class=ToolClass.WRITE_STATE,
            input_model=QuoteAcceptRequest,
            output_model=PricingTimelineQuote,
            handler=accept_handler,
            timeout_seconds=2.0,
            retry_policy=RetryPolicy(attempts=1),
        )
    )
    registry.register(
        ToolDefinition(
            name="pricing.config.validate.v1",
            tool_class=ToolClass.READ,
            input_model=EmptyPricingRequest,
            output_model=ConfigValidationResult,
            handler=config_validate_handler,
            timeout_seconds=2.0,
            retry_policy=RetryPolicy(attempts=1),
        )
    )
    registry.register(
        ToolDefinition(
            name="get_pricing_quote.v1",
            tool_class=ToolClass.WRITE_STATE,
            input_model=LegacyPricingQuoteRequest,
            output_model=LegacyPricingQuoteResponse,
            handler=legacy_quote_handler,
            timeout_seconds=5.0,
            retry_policy=RetryPolicy(attempts=2),
        )
    )
    registry.register(
        ToolDefinition(
            name="get_timeline_estimate.v1",
            tool_class=ToolClass.READ,
            input_model=LegacyTimelineEstimateRequest,
            output_model=LegacyTimelineEstimateResponse,
            handler=legacy_timeline_handler,
            timeout_seconds=5.0,
            retry_policy=RetryPolicy(attempts=2),
        )
    )
    registry.register(
        ToolDefinition(
            name="list_required_quote_inputs.v1",
            tool_class=ToolClass.READ,
            input_model=LegacyRequiredInputsRequest,
            output_model=LegacyRequiredInputsResponse,
            handler=legacy_required_handler,
            timeout_seconds=2.0,
            retry_policy=RetryPolicy(attempts=1),
        )
    )


def _legacy_to_v2(request: LegacyPricingQuoteRequest) -> PricingQuoteRequest:
    service = str(request.service)
    return PricingQuoteRequest.model_validate(
        {
            "thread_id": str(request.thread_id),
            "customer_id": str(request.customer_id) if request.customer_id else None,
            "requested_services": [service],
            "service_inputs": {
                service: _legacy_service_inputs(
                    service=service,
                    word_count=request.word_count,
                    page_count=request.page_count,
                    genre=request.genre,
                    tier=request.tier,
                )
            },
            "global_inputs": {
                "genre": request.genre,
                "word_count": request.word_count,
                "page_count": request.page_count,
            },
        }
    )


def _legacy_service_inputs(
    *,
    service: str,
    word_count: int | None,
    page_count: int | None,
    genre: str | None,
    tier: str | None,
) -> dict[str, Any]:
    del tier
    if service == "ghostwriting":
        return {
            "service_type": "full_ghostwriting",
            "category": "fiction_standard" if genre else None,
            "word_count": word_count,
            "manuscript_status": "outline_ready",
        }
    if service == "editing_proofreading":
        return {
            "service_type": "copy_editing",
            "category": "standard_fiction",
            "word_count": word_count,
            "manuscript_condition": "average",
        }
    if service == "interior_formatting":
        return {"output_format": "print_ebook", "category": "fiction", "page_count": page_count}
    return {"tier": "professional"}


def _legacy_quote_response(
    request: LegacyPricingQuoteRequest,
    quote: PricingTimelineQuote,
) -> LegacyPricingQuoteResponse:
    if quote.missing_inputs:
        return LegacyPricingQuoteResponse(
            service=request.service,
            missing_inputs=[item.field for item in quote.missing_inputs],
            confidence=quote.confidence,
            human_review_required=False,
            suggested_phrasing=quote.missing_inputs[0].question,
        )
    if quote.status == QuoteStatus.HUMAN_REVIEW_REQUIRED and not quote.line_items:
        return LegacyPricingQuoteResponse(
            service=request.service,
            risk_flags=[warning.code for warning in quote.warnings],
            human_review_required=True,
            confidence=0.0,
            suggested_phrasing=(
                "I can scope this, but BookCraft's v2.1 pricing values are not approved "
                "for customer-facing use yet. I won't guess at numbers."
            ),
        )
    return LegacyPricingQuoteResponse(
        quote_id=quote.quote_id,
        service=request.service,
        confidence=quote.confidence,
        risk_flags=[warning.code for warning in quote.warnings],
        human_review_required=quote.human_review_required,
        suggested_phrasing="Deterministic pricing range returned by PricingTimelineEngine v2.1.",
    )


def _legacy_timeline_response(
    request: LegacyTimelineEstimateRequest,
    quote: PricingTimelineQuote,
) -> LegacyTimelineEstimateResponse:
    if quote.missing_inputs:
        return LegacyTimelineEstimateResponse(
            service=request.service,
            missing_inputs=[item.field for item in quote.missing_inputs],
            confidence=quote.confidence,
            suggested_phrasing=quote.missing_inputs[0].question,
        )
    if quote.status == QuoteStatus.HUMAN_REVIEW_REQUIRED and not quote.line_items:
        return LegacyTimelineEstimateResponse(
            service=request.service,
            risk_flags=[warning.code for warning in quote.warnings],
            human_review_required=True,
            confidence=0.0,
            suggested_phrasing=(
                "I can scope this, but BookCraft's v2.1 timeline values are not approved "
                "for customer-facing use yet. I won't guess at timing."
            ),
        )
    return LegacyTimelineEstimateResponse(
        estimate_id=quote.quote_id,
        service=request.service,
        confidence=quote.confidence,
        risk_flags=[warning.code for warning in quote.warnings],
        human_review_required=quote.human_review_required,
        suggested_phrasing="Deterministic timeline range returned by PricingTimelineEngine v2.1.",
    )
