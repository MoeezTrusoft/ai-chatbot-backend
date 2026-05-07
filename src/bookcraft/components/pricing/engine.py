from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from .calculators import calculate_service_line_item
from .config import EngineConfig, load_engine_config, validate_engine_config
from .discounts import apply_discounts
from .metrics import PricingMetrics
from .models import (
    DiscountLine,
    DurationRange,
    MissingInput,
    Money,
    MoneyRange,
    PaymentScheduleOption,
    PricingQuoteRequest,
    PricingTimelineQuote,
    ProjectTimeline,
    QuoteLineItem,
    QuoteStatus,
    QuoteWarning,
    ServiceCategory,
)
from .persistence import InMemoryQuoteRepository
from .schedule import build_project_timeline


class PricingTimelineEngine:
    def __init__(
        self,
        config: EngineConfig,
        repository: InMemoryQuoteRepository | None = None,
        metrics: PricingMetrics | None = None,
        values_approved: bool = False,
    ) -> None:
        validation = validate_engine_config(config)
        if not validation.valid:
            raise ValueError("Invalid pricing config: " + "; ".join(validation.errors))
        self.config = config
        self.repository = repository or InMemoryQuoteRepository()
        self.metrics = metrics or PricingMetrics()
        self.values_approved = values_approved

    @classmethod
    def from_config_dir(
        cls,
        config_dir: str | Path,
        *,
        values_approved: bool = False,
    ) -> PricingTimelineEngine:
        return cls(load_engine_config(config_dir), values_approved=values_approved)

    def list_required_inputs(
        self, services: list[ServiceCategory]
    ) -> dict[ServiceCategory, list[MissingInput]]:
        result: dict[ServiceCategory, list[MissingInput]] = {}
        for service in services:
            cfg = self.config.service_configs[ServiceCategory(service)]
            result[ServiceCategory(service)] = [
                MissingInput(service=cfg.service, field=field.field, question=field.question)
                for field in cfg.required_inputs
            ]
        return result

    def quote(self, request: PricingQuoteRequest) -> PricingTimelineQuote:
        requested_services = [ServiceCategory(service) for service in request.requested_services]
        missing = self._missing_inputs(request, requested_services)
        for miss in missing:
            self.metrics.record_missing(miss.service.value, miss.field)
        if missing:
            quote = self._clarification_quote(request, requested_services, missing)
            self.repository.save_quote(quote)
            for service in requested_services:
                self.metrics.record_status(service.value, str(quote.status))
            return quote
        if not self.values_approved:
            quote = self._values_not_approved_quote(request, requested_services)
            self.repository.save_quote(quote)
            for service in requested_services:
                self.metrics.record_status(service.value, str(quote.status))
                self.metrics.record_review(service.value, "VALUES_NOT_APPROVED")
            return quote

        line_items: list[QuoteLineItem] = []
        all_warnings: list[QuoteWarning] = []
        assumptions: list[str] = []
        exclusions: list[str] = []
        for service in requested_services:
            service_inputs = request.service_inputs.get(service.value, {})
            cfg = self.config.service_configs[service]
            with self.metrics.latency(service.value):
                item = calculate_service_line_item(cfg, request, service_inputs)
            line_items.append(item)
            all_warnings.extend(item.warnings)
            assumptions.extend(item.assumptions)
            exclusions.extend(cfg.exclusions)
            midpoint = float((item.final_price_range.low.amount + item.final_price_range.high.amount) / 2)
            self.metrics.record_value(service.value, request.quote_mode, midpoint)
            width = (item.final_price_range.high.amount - item.final_price_range.low.amount) / max(
                Decimal("1"), midpoint and Decimal(str(midpoint)) or Decimal("1")
            )
            self.metrics.record_range_width(service.value, float(width))

        currency = line_items[0].base_price.currency if line_items else "USD"
        subtotal_range = self._sum_ranges([item.final_price_range for item in line_items], currency)
        discount_lines = apply_discounts(request, line_items, self.config.discount_policy, currency)
        discount_low, discount_high = self._discount_totals(discount_lines, currency)
        total_range = MoneyRange(
            low=subtotal_range.low - discount_high,
            high=subtotal_range.high - discount_low,
        )
        timeline = build_project_timeline(line_items, self.config.dependency_graph)
        human_review = any(item.human_review_required for item in line_items) or any(
            warning.requires_human_review for warning in all_warnings
        ) or any(discount.requires_human_review for discount in discount_lines)
        for warning in all_warnings:
            if warning.requires_human_review:
                self.metrics.record_review(str(warning.service or requested_services[0]), warning.code)
        for discount in discount_lines:
            if discount.requires_human_review:
                self.metrics.record_review(str(requested_services[0]), discount.code)
        status = (
            QuoteStatus.HUMAN_REVIEW_REQUIRED
            if human_review
            else QuoteStatus.FORMAL_QUOTE_READY
            if request.quote_mode in {"formal_quote", "agreement_ready"}
            else QuoteStatus.ESTIMATED
        )
        confidence = self.config.quote_policy.confidence_with_warnings if all_warnings else self.config.quote_policy.confidence_complete
        quote = PricingTimelineQuote(
            config_versions=self.config.versions,
            status=status,
            requested_services=requested_services,
            line_items=line_items,
            subtotal_range=subtotal_range,
            discount_lines=discount_lines,
            total_price_range=total_range,
            timeline=timeline,
            payment_schedule_options=self._payment_options(total_range),
            assumptions=sorted(set(assumptions)),
            exclusions=sorted(set(exclusions)),
            missing_inputs=[],
            warnings=all_warnings,
            confidence=confidence,
            human_review_required=human_review,
            audit_trace={
                "engine_version": "2.2.0",
                "rule": "All commercial numbers produced deterministically by PricingTimelineEngine.",
            },
        )
        self.repository.save_quote(quote)
        for service in requested_services:
            self.metrics.record_status(service.value, str(quote.status))
        return quote

    def _values_not_approved_quote(
        self,
        request: PricingQuoteRequest,
        services: list[ServiceCategory],
    ) -> PricingTimelineQuote:
        zero = Money(amount=0, currency="USD")
        return PricingTimelineQuote(
            config_versions=self.config.versions,
            status=QuoteStatus.HUMAN_REVIEW_REQUIRED,
            requested_services=services,
            line_items=[],
            subtotal_range=MoneyRange(low=zero, high=zero),
            discount_lines=[],
            total_price_range=MoneyRange(low=zero, high=zero),
            timeline=build_empty_timeline(),
            payment_schedule_options=[],
            assumptions=[],
            exclusions=[],
            missing_inputs=[],
            warnings=[
                QuoteWarning(
                    code="VALUES_NOT_APPROVED",
                    message=(
                        "Pricing v2.2 values are installed but not approved for "
                        "customer-facing contractual use."
                    ),
                    requires_human_review=True,
                )
            ],
            confidence=0.0,
            human_review_required=True,
            audit_trace={
                "engine_version": "2.2.0",
                "blocked_reason": "pricing_values_not_approved",
                "quote_mode": request.quote_mode,
            },
        )

    def accept_quote(self, quote_id: Any, confirmed_by: str = "user") -> PricingTimelineQuote:
        quote = self.repository.get_quote(quote_id)
        if quote is None:
            raise KeyError(f"Quote not found: {quote_id}")
        if quote.status not in {QuoteStatus.ESTIMATED, QuoteStatus.FORMAL_QUOTE_READY}:
            raise ValueError(f"Quote cannot be accepted from status {quote.status}")
        quote.status = QuoteStatus.ACCEPTED
        self.repository.append_event(str(quote.quote_id), "quote.accepted", {"confirmed_by": confirmed_by})  # type: ignore[arg-type]
        return quote

    def _missing_inputs(
        self, request: PricingQuoteRequest, services: list[ServiceCategory]
    ) -> list[MissingInput]:
        missing: list[MissingInput] = []
        global_inputs = request.global_inputs.model_dump()
        for service in services:
            cfg = self.config.service_configs[service]
            service_inputs = request.service_inputs.get(service.value, {})
            for required in cfg.required_inputs:
                value = service_inputs.get(required.field)
                if value is None and required.fallback_global_field:
                    value = global_inputs.get(required.fallback_global_field)
                if value is None or value == "":
                    missing.append(
                        MissingInput(
                            service=service,
                            field=required.field,
                            question=required.question,
                            severity="required",
                        )
                    )
        return missing

    def _clarification_quote(
        self,
        request: PricingQuoteRequest,
        services: list[ServiceCategory],
        missing: list[MissingInput],
    ) -> PricingTimelineQuote:
        zero = Money(amount=0, currency="USD")
        return PricingTimelineQuote(
            config_versions=self.config.versions,
            status=QuoteStatus.NEEDS_CLARIFICATION,
            requested_services=services,
            line_items=[],
            subtotal_range=MoneyRange(low=zero, high=zero),
            discount_lines=[],
            total_price_range=MoneyRange(low=zero, high=zero),
            timeline=build_empty_timeline(),
            payment_schedule_options=[],
            assumptions=[],
            exclusions=[],
            missing_inputs=missing,
            warnings=[
                QuoteWarning(
                    code="MISSING_REQUIRED_INPUTS",
                    message="The engine cannot return pricing or timeline numbers until required inputs are supplied.",
                )
            ],
            confidence=0.0,
            human_review_required=False,
            audit_trace={"engine_version": "2.2.0", "blocked_reason": "missing_required_inputs"},
        )

    @staticmethod
    def _sum_ranges(ranges: list[MoneyRange], currency: str) -> MoneyRange:
        low = sum((r.low.amount for r in ranges), Decimal("0"))
        high = sum((r.high.amount for r in ranges), Decimal("0"))
        return MoneyRange(low=Money(amount=low, currency=currency), high=Money(amount=high, currency=currency))

    @staticmethod
    def _discount_totals(discounts: list[DiscountLine], currency: str) -> tuple[Money, Money]:
        total = sum((d.amount.amount for d in discounts), Decimal("0"))
        return Money(amount=total, currency=currency), Money(amount=total, currency=currency)

    def _payment_options(self, total_range: MoneyRange) -> list[PaymentScheduleOption]:
        options: list[PaymentScheduleOption] = []
        midpoint = (total_range.low.amount + total_range.high.amount) / Decimal("2")
        for code, cfg in self.config.payment_schedule_policy.options.items():
            payments = []
            for part in cfg.get("payments", []):
                pct = Decimal(str(part["percent"]))
                payments.append(
                    {
                        "label": part["label"],
                        "percent": str(pct),
                        "estimated_amount": str((midpoint * pct / Decimal("100")).quantize(Decimal("0.01"))),
                        "trigger": part.get("trigger"),
                    }
                )
            options.append(
                PaymentScheduleOption(
                    code=code,
                    label=cfg.get("label", code),
                    description=cfg.get("description", ""),
                    payments=payments,
                )
            )
        return options


def build_empty_timeline() -> ProjectTimeline:
    from .models import ProjectTimeline

    return ProjectTimeline(total_timeline=DurationRange(low=0, high=0, unit="business_days"), schedule=[])
