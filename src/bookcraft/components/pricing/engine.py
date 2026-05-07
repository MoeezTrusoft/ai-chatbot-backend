from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import structlog
from prometheus_client import Counter, Histogram

from bookcraft.components.pricing.rules import PricingRuleSet, load_pricing_rules
from bookcraft.components.pricing.schemas import (
    MoneyRange,
    PricingCalculationError,
    PricingConfigurationError,
    PricingQuoteRequest,
    PricingQuoteResponse,
    QuoteLineItem,
    RequiredInputsRequest,
    RequiredInputsResponse,
    TimelineEstimateRequest,
    TimelineEstimateResponse,
    TimelineRange,
    ensure_decimal,
)
from bookcraft.domain.enums import ServiceCategory

PRICING_QUOTES_TOTAL = Counter(
    "pricing_quotes_total",
    "Pricing quote attempts.",
    ["service", "status"],
)
PRICING_QUOTE_FAILURES = Counter(
    "pricing_quote_failures_total",
    "Pricing quote failures.",
    ["service", "reason"],
)
PRICING_QUOTE_SECONDS = Histogram("pricing_quote_latency_seconds", "Pricing quote latency.")
PRICING_MISSING_INPUTS = Counter(
    "pricing_missing_inputs_total",
    "Missing pricing inputs.",
    ["service", "field"],
)
PRICING_HUMAN_REVIEW = Counter(
    "pricing_human_review_total",
    "Pricing quotes requiring human review.",
    ["service", "reason"],
)


@dataclass(frozen=True, slots=True)
class PricingTimelineEngine:
    rule_set: PricingRuleSet
    allow_placeholder_rules: bool = False

    @classmethod
    def from_rule_dir(
        cls,
        rule_dir: Path,
        *,
        allow_placeholder_rules: bool = False,
    ) -> PricingTimelineEngine:
        return cls(
            rule_set=load_pricing_rules(rule_dir),
            allow_placeholder_rules=allow_placeholder_rules,
        )

    def list_required_inputs(self, request: RequiredInputsRequest) -> RequiredInputsResponse:
        missing = self.missing_inputs_for(
            service=request.service,
            tier=request.tier,
            word_count=request.word_count,
            page_count=request.page_count,
            genre=request.genre,
        )
        return RequiredInputsResponse(
            service=request.service,
            missing_inputs=missing,
            suggested_question=suggested_question(missing),
        )

    def quote(self, request: PricingQuoteRequest) -> PricingQuoteResponse:
        with PRICING_QUOTE_SECONDS.time():
            missing = self.missing_inputs_for(
                service=request.service,
                tier=request.tier,
                word_count=request.word_count,
                page_count=request.page_count,
                genre=request.genre,
            )
            if missing:
                for field in missing:
                    PRICING_MISSING_INPUTS.labels(service=request.service.value, field=field).inc()
                PRICING_QUOTES_TOTAL.labels(
                    service=request.service.value,
                    status="needs_clarification",
                ).inc()
                return PricingQuoteResponse(
                    service=request.service,
                    missing_inputs=missing,
                    confidence=0.0,
                    human_review_required=False,
                    suggested_phrasing=suggested_question(missing),
                )
            if self.rule_set.has_placeholders and not self.allow_placeholder_rules:
                PRICING_QUOTE_FAILURES.labels(
                    service=request.service.value,
                    reason="placeholder_rules",
                ).inc()
                PRICING_QUOTES_TOTAL.labels(service=request.service.value, status="blocked").inc()
                return PricingQuoteResponse(
                    service=request.service,
                    risk_flags=["pricing_rules_not_approved"],
                    human_review_required=True,
                    confidence=0.0,
                    suggested_phrasing=(
                        "I can scope this, but BookCraft's approved pricing rules are not "
                        "available in this environment yet. I won't guess at numbers."
                    ),
                )
            try:
                response = self._calculate_quote(request)
            except Exception as exc:
                PRICING_QUOTE_FAILURES.labels(
                    service=request.service.value,
                    reason="calculation",
                ).inc()
                raise PricingCalculationError(str(exc)) from exc
            status = "human_review" if response.human_review_required else "quoted"
            PRICING_QUOTES_TOTAL.labels(service=request.service.value, status=status).inc()
            structlog.get_logger(__name__).info(
                "pricing_quote_created",
                service=request.service.value,
                quote_id=str(response.quote_id),
                rule_checksum=self.rule_set.checksum,
                status=status,
            )
            return response

    def timeline(self, request: TimelineEstimateRequest) -> TimelineEstimateResponse:
        missing = self.missing_inputs_for(
            service=request.service,
            tier=request.tier,
            word_count=request.word_count,
            page_count=request.page_count,
            genre=request.genre,
        )
        if missing:
            for field in missing:
                PRICING_MISSING_INPUTS.labels(service=request.service.value, field=field).inc()
            return TimelineEstimateResponse(
                service=request.service,
                missing_inputs=missing,
                confidence=0.0,
                suggested_phrasing=suggested_question(missing),
            )
        if self.rule_set.has_placeholders and not self.allow_placeholder_rules:
            return TimelineEstimateResponse(
                service=request.service,
                risk_flags=["pricing_rules_not_approved"],
                human_review_required=True,
                confidence=0.0,
                suggested_phrasing=(
                    "I can scope this, but BookCraft's approved timeline rules are not "
                    "available in this environment yet. I won't guess at timing."
                ),
            )
        quote = self._calculate_quote(request)
        return TimelineEstimateResponse(
            service=request.service,
            timeline_range=quote.total_timeline_range,
            assumptions=quote.assumptions,
            exclusions=quote.exclusions,
            confidence=quote.confidence,
            risk_flags=quote.risk_flags,
            human_review_required=quote.human_review_required,
            suggested_phrasing=_timeline_phrase(quote),
        )

    def missing_inputs_for(
        self,
        *,
        service: ServiceCategory,
        tier: str | None,
        word_count: int | None,
        page_count: int | None,
        genre: str | None,
    ) -> list[str]:
        entry = self.rule_set.catalog.services[service]
        missing: list[str] = []
        facts: dict[str, object | None] = {
            "tier": tier,
            "word_count": word_count,
            "page_count": page_count,
            "genre": genre,
        }
        for required in entry.required_inputs:
            fact = facts.get(required)
            if fact is None or fact == "":
                missing.append(required)
        return missing

    def _calculate_quote(self, request: PricingQuoteRequest) -> PricingQuoteResponse:
        tier = request.tier or self.rule_set.catalog.services[request.service].default_tier
        price_rule = self.rule_set.pricing.rules[request.service]
        timeline_rule = self.rule_set.timeline.rules[request.service]
        if tier not in price_rule.rates or tier not in timeline_rule.base_days:
            msg = f"unsupported tier for {request.service.value}: {tier}"
            raise PricingCalculationError(msg)

        base_price = _formula_amount(
            formula=price_rule.formula,
            rate=ensure_decimal(
                price_rule.rates[tier],
                field_name=f"pricing.{request.service.value}.{tier}",
            ),
            word_count=request.word_count,
            page_count=request.page_count,
        )
        low_multiplier = ensure_decimal(
            self.rule_set.pricing.range_multiplier.get("low", "1"),
            field_name="pricing.range_multiplier.low",
        )
        high_multiplier = ensure_decimal(
            self.rule_set.pricing.range_multiplier.get("high", "1"),
            field_name="pricing.range_multiplier.high",
        )
        low_price = _money(base_price * low_multiplier)
        high_price = _money(base_price * high_multiplier)
        base_days = _formula_days(
            formula=timeline_rule.formula,
            base_days=ensure_decimal(
                timeline_rule.base_days[tier],
                field_name=f"timeline.{request.service.value}.{tier}",
            ),
            word_count=request.word_count,
            page_count=request.page_count,
        )
        low_padding = int(
            ensure_decimal(
                self.rule_set.timeline.range_padding_days.get("low", "0"),
                field_name="timeline.range_padding_days.low",
            )
        )
        high_padding = int(
            ensure_decimal(
                self.rule_set.timeline.range_padding_days.get("high", "0"),
                field_name="timeline.range_padding_days.high",
            )
        )
        timeline_range = TimelineRange(
            low=max(0, base_days + low_padding),
            high=max(base_days + low_padding, base_days + high_padding),
        )
        money_range = MoneyRange(
            currency=self.rule_set.pricing.currency,
            low=low_price,
            high=high_price,
        )
        assumptions = list(self.rule_set.policy.assumptions)
        assumptions.append(f"Service tier used for calculation: {tier}.")
        confidence, risk_flags = _confidence_and_risks(request)
        if request.urgency and request.urgency.lower() in {"rush", "urgent", "asap"}:
            risk_flags.append("rush_request_requires_review")
        human_review_required = (
            confidence < self.rule_set.policy.human_review.low_confidence_threshold
        )
        for reason in risk_flags:
            PRICING_HUMAN_REVIEW.labels(service=request.service.value, reason=reason).inc()
        human_review_required = human_review_required or bool(risk_flags)
        line_item = QuoteLineItem(
            service=request.service,
            tier=tier,
            price_range=money_range,
            timeline_range=timeline_range,
            assumptions=assumptions,
        )
        response = PricingQuoteResponse(
            service=request.service,
            line_items=[line_item],
            total_price_range=money_range,
            total_timeline_range=timeline_range,
            currency=self.rule_set.pricing.currency,
            valid_until=datetime.now(UTC)
            + timedelta(days=self.rule_set.policy.quote_valid_days),
            assumptions=assumptions,
            exclusions=list(self.rule_set.policy.exclusions),
            confidence=confidence,
            risk_flags=risk_flags,
            human_review_required=human_review_required,
            suggested_phrasing=_quote_phrase(request.service, money_range, timeline_range),
        )
        return response


def suggested_question(missing_inputs: list[str]) -> str:
    if not missing_inputs:
        return "I have the required inputs to prepare a deterministic quote."
    first = missing_inputs[0]
    prompts = {
        "service": "Which BookCraft service should I price?",
        "tier": "Which service tier should I use: basic, standard, or premium?",
        "word_count": (
            "To use the deterministic quote engine, approximately how many words "
            "is your manuscript?"
        ),
        "page_count": "Approximately how many pages is your manuscript?",
        "genre": "What genre is the book?",
    }
    return prompts.get(first, f"Please share this quote detail: {first}.")


def _formula_amount(
    *,
    formula: str,
    rate: Decimal,
    word_count: int | None,
    page_count: int | None,
) -> Decimal:
    if formula == "word_count_rate":
        if word_count is None:
            raise PricingCalculationError("word_count is required")
        return rate * Decimal(word_count)
    if formula == "page_count_rate":
        if page_count is None:
            raise PricingCalculationError("page_count is required")
        return rate * Decimal(page_count)
    if formula == "flat_project":
        return rate
    raise PricingConfigurationError(f"unsupported pricing formula: {formula}")


def _formula_days(
    *,
    formula: str,
    base_days: Decimal,
    word_count: int | None,
    page_count: int | None,
) -> int:
    if formula == "word_count_days":
        count = word_count or 0
        return int(base_days + Decimal(count // 10000))
    if formula == "page_count_days":
        count = page_count or 0
        return int(base_days + Decimal(count // 100))
    if formula == "flat_days":
        return int(base_days)
    raise PricingConfigurationError(f"unsupported timeline formula: {formula}")


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _confidence_and_risks(request: PricingQuoteRequest) -> tuple[float, list[str]]:
    confidence = request.confidence or 0.75
    risk_flags: list[str] = []
    if not request.genre:
        confidence -= 0.1
    if request.add_ons:
        risk_flags.append("add_ons_require_review")
    if request.complexity and request.complexity.lower() in {"high", "complex"}:
        risk_flags.append("high_complexity_requires_review")
    return max(0.0, min(1.0, confidence)), risk_flags


def _quote_phrase(
    service: ServiceCategory,
    price: MoneyRange,
    timeline: TimelineRange,
) -> str:
    return (
        f"For {service.value.replace('_', ' ')}, the deterministic engine returned "
        f"a {price.currency} {price.low}-{price.high} range and "
        f"{timeline.low}-{timeline.high} business days, subject to assumptions."
    )


def _timeline_phrase(quote: PricingQuoteResponse) -> str:
    timeline = quote.total_timeline_range
    if timeline is None:
        return "The deterministic engine could not produce a timeline range."
    return (
        f"The deterministic engine returned a {timeline.low}-{timeline.high} business day range, "
        "subject to assumptions."
    )
