from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from bookcraft.components.pricing import PricingTimelineEngine
from bookcraft.components.pricing.models import (
    PricingQuoteRequest,
    QuoteGlobalInputs,
    QuoteStatus,
    ServiceCategory,
)
from bookcraft.components.pricing_actions.schemas import (
    PricingActionRequest,
    PricingActionResult,
)


class PricingQuoteRepositoryProtocol(Protocol):
    async def save_quote(
        self,
        *,
        quote_id: UUID,
        lead_id: UUID | None,
        customer_id: UUID | None,
        thread_id: UUID,
        services: list[str],
        input_params: dict[str, Any],
        used_default_assumptions: bool,
        assumptions: dict[str, Any] | None,
        quote_output: dict[str, Any],
        customer_safe_summary: str,
        status: str,
    ) -> object: ...


@dataclass(slots=True)
class PricingActionService:
    pricing_engine: PricingTimelineEngine
    repository: PricingQuoteRepositoryProtocol
    default_assumptions_path: Path = Path("data/pricing/default_assumptions.json")

    async def quote(self, request: PricingActionRequest) -> PricingActionResult:
        assumptions = self._load_default_assumptions() if request.use_default_assumptions else {}
        engine_request = self._build_engine_request(request, assumptions)
        quote = self.pricing_engine.quote(engine_request)

        quote_output = quote.model_dump(mode="json")
        quote_status = _enum_or_string_value(quote.status)
        quote_services = [_enum_or_string_value(service) for service in quote.requested_services]
        missing_fields = sorted({missing.field for missing in quote.missing_inputs})
        customer_safe_summary = _customer_safe_summary(
            status=quote_status,
            missing_fields=missing_fields,
            used_default_assumptions=request.use_default_assumptions,
            services=quote_services,
        )

        await self.repository.save_quote(
            quote_id=quote.quote_id,
            lead_id=request.lead_id,
            customer_id=request.customer_id,
            thread_id=request.thread_id,
            services=quote_services,
            input_params=engine_request.model_dump(mode="json"),
            used_default_assumptions=request.use_default_assumptions,
            assumptions=assumptions if request.use_default_assumptions else None,
            quote_output=quote_output,
            customer_safe_summary=customer_safe_summary,
            status=quote_status,
        )

        return PricingActionResult(
            quote_id=quote.quote_id,
            status=quote_status,
            services=quote_services,
            missing_fields=missing_fields,
            used_default_assumptions=request.use_default_assumptions,
            assumptions=assumptions if request.use_default_assumptions else None,
            customer_safe_summary=customer_safe_summary,
            quote_output=quote_output,
        )

    def _build_engine_request(
        self,
        request: PricingActionRequest,
        assumptions: dict[str, Any],
    ) -> PricingQuoteRequest:
        services = [
            ServiceCategory(service)
            for service in request.services
            if service in set(item.value for item in ServiceCategory)
        ]
        if not services:
            raise ValueError("Pricing action requires at least one valid service.")

        global_inputs: dict[str, Any] = {}
        service_inputs: dict[str, dict[str, Any]] = {}

        if request.use_default_assumptions:
            for service in services:
                service_assumptions = assumptions.get(service.value, {})
                global_inputs.update(service_assumptions.get("global_inputs", {}))
                service_inputs[service.value] = dict(service_assumptions.get("service_inputs", {}))

        global_inputs.update(_global_inputs_from_slots(request.collected_slots))

        for service in services:
            service_inputs.setdefault(service.value, {})
            service_inputs[service.value].update(
                _service_inputs_from_slots(request.collected_slots)
            )

        return PricingQuoteRequest(
            thread_id=request.thread_id,
            customer_id=request.customer_id,
            requested_services=services,
            service_inputs=service_inputs,
            global_inputs=QuoteGlobalInputs(**global_inputs),
            quote_mode="estimate",
        )

    def _load_default_assumptions(self) -> dict[str, Any]:
        if not self.default_assumptions_path.exists():
            return {}
        data = json.loads(self.default_assumptions_path.read_text())
        return data if isinstance(data, dict) else {}


def _enum_or_string_value(value: object) -> str:
    raw_value = getattr(value, "value", value)
    return str(raw_value)


def _global_inputs_from_slots(slots: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}

    for key in [
        "genre",
        "word_count",
        "page_count",
        "manuscript_status",
        "deadline",
    ]:
        value = slots.get(key)
        if value is not None:
            target_key = "launch_goal" if key == "deadline" else key
            data[target_key] = value

    platforms = slots.get("platforms") or slots.get("format_targets")
    if isinstance(platforms, list):
        data["format_targets"] = [str(item) for item in platforms]
    elif platforms is not None:
        data["format_targets"] = [str(platforms)]

    return data


def _service_inputs_from_slots(slots: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}

    for key in [
        "editing_level",
        "manuscript_condition",
        "complexity",
        "image_table_complexity",
        "cover_type",
        "platforms",
        "distribution_scope",
        "scope",
        "launch_window",
    ]:
        value = slots.get(key)
        if value is not None:
            data[key] = value

    return data


def _customer_safe_summary(
    *,
    status: str,
    missing_fields: list[str],
    used_default_assumptions: bool,
    services: list[str],
) -> str:
    service_text = ", ".join(services) if services else "the requested services"

    if status == QuoteStatus.NEEDS_CLARIFICATION.value:
        fields = ", ".join(missing_fields) if missing_fields else "more project details"
        return f"I need {fields} before preparing a reliable estimate for {service_text}."

    if status == QuoteStatus.HUMAN_REVIEW_REQUIRED.value:
        return (
            "I can prepare the scope, but this estimate needs human review before it "
            "is treated as customer-facing pricing."
        )

    if used_default_assumptions:
        return (
            f"I prepared an example estimate for {service_text} using standard "
            "assumptions so you can react to a starting point."
        )

    return f"I prepared an estimate for {service_text} using the available project details."
