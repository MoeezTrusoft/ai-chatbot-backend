from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from .config import AddOnConfig, ServiceConfig
from .models import (
    AddOnLine,
    ComplexityContribution,
    Money,
    MoneyRange,
    QuoteWarning,
    RequestedTimeline,
)


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ceil_days(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_CEILING)


def nested_lookup(mapping: Any, path: list[str]) -> Any:
    current = mapping
    for raw_key in path:
        key = str(raw_key)
        if isinstance(current, dict):
            normalized = {str(k).lower(): v for k, v in current.items()}
            if key.lower() not in normalized:
                raise KeyError(f"Path segment {key!r} not found in {list(current.keys())}")
            current = normalized[key.lower()]
        else:
            raise KeyError(f"Cannot look up {key!r} in non-mapping value")
    return current


def money(amount: Decimal | int | str | float, currency: str = "USD") -> Money:
    return Money(amount=amount, currency=currency)


def compute_complexity(
    service_config: ServiceConfig, service_inputs: dict[str, Any]
) -> tuple[Decimal, list[ComplexityContribution], dict[str, Decimal]]:
    contributions: list[ComplexityContribution] = []
    group_points: dict[str, Decimal] = {}
    for driver in service_config.complexity.drivers:
        selected = service_inputs.get(driver.field)
        if selected is None:
            continue
        selected_values = selected if isinstance(selected, list) else [selected]
        for selected_value in selected_values:
            option = driver.option_for(selected_value)
            if option is None:
                continue
            group_points[option.group] = group_points.get(option.group, Decimal("0")) + option.points
            contributions.append(
                ComplexityContribution(
                    driver=driver.label,
                    selected_value=selected_value,
                    points=option.points,
                    reason=option.reason,
                )
            )
    total_points = sum(group_points.values(), Decimal("0"))
    if service_config.complexity.mode == "ghostwriting_weighted":
        factor = Decimal("1")
        coefficients = service_config.complexity.weighted_coefficients
        for group, points in group_points.items():
            factor += coefficients.get(group, Decimal("0.005")) * points
    else:
        factor = Decimal("1") + total_points * service_config.complexity.point_multiplier
    factor = min(factor, service_config.complexity.max_factor)
    factor = max(factor, Decimal("1"))
    return q2(factor), contributions, group_points


def compute_addons(
    service_config: ServiceConfig,
    service_inputs: dict[str, Any],
    global_inputs: dict[str, Any],
) -> tuple[list[AddOnLine], Money, Decimal, Decimal, list[QuoteWarning]]:
    selected_codes = service_inputs.get("add_ons", []) or []
    if isinstance(selected_codes, str):
        selected_codes = [selected_codes]
    lines: list[AddOnLine] = []
    total_price = money(0, service_config.currency)
    total_days = Decimal("0")
    total_cp = Decimal("0")
    warnings: list[QuoteWarning] = []
    for code in selected_codes:
        addon = service_config.addon_by_code(str(code))
        if addon is None:
            warnings.append(
                QuoteWarning(
                    code="UNKNOWN_ADDON",
                    message=f"Unknown add-on {code!r}; human review required.",
                    service=service_config.service,
                    requires_human_review=True,
                )
            )
            continue
        quantity = _addon_quantity(addon, service_inputs, global_inputs)
        line_price, line_days = _calculate_addon_amounts(addon, quantity, service_config.currency)
        lines.append(
            AddOnLine(
                code=addon.code,
                title=addon.title,
                quantity=quantity,
                price=line_price,
                duration_days=q2(line_days),
                complexity_points=addon.complexity_points,
            )
        )
        total_price += line_price
        total_days += line_days
        total_cp += addon.complexity_points
        if addon.human_review_required:
            warnings.append(
                QuoteWarning(
                    code="ADDON_REQUIRES_REVIEW",
                    message=f"Add-on {addon.title} requires human review.",
                    service=service_config.service,
                    requires_human_review=True,
                )
            )
    return lines, total_price, q2(total_days), total_cp, warnings


def _addon_quantity(addon: AddOnConfig, service_inputs: dict[str, Any], global_inputs: dict[str, Any]) -> Decimal:
    if addon.unit_field is None:
        return Decimal("1")
    if addon.unit_field in service_inputs and service_inputs[addon.unit_field] is not None:
        return d(service_inputs[addon.unit_field])
    if addon.unit_field in global_inputs and global_inputs[addon.unit_field] is not None:
        return d(global_inputs[addon.unit_field])
    return Decimal("1")


def _calculate_addon_amounts(
    addon: AddOnConfig, quantity: Decimal, currency: str
) -> tuple[Money, Decimal]:
    if addon.price_type == "quote_only":
        price = money(0, currency)
    elif addon.price_type == "fixed":
        price = money(addon.price or 0, currency)
    elif addon.price_type == "per_unit":
        price = money((addon.price or Decimal("0")) * quantity, currency)
    else:
        raise ValueError(f"Unsupported add-on price type: {addon.price_type}")

    if addon.duration_type in {"none", "quote_only"}:
        duration = Decimal("0")
    elif addon.duration_type == "fixed_days":
        duration = addon.duration_days or Decimal("0")
    elif addon.duration_type == "per_unit_pace":
        duration = quantity / (addon.pace_per_day or Decimal("1"))
    else:
        raise ValueError(f"Unsupported add-on duration type: {addon.duration_type}")
    return price, duration


def compute_schedule_multiplier(
    service_config: ServiceConfig,
    complexity_duration_days: Decimal,
    requested_timeline: RequestedTimeline | None,
) -> tuple[Decimal, Decimal, str, bool, list[QuoteWarning], dict[str, Any]]:
    policy = service_config.timeline_policy
    warnings: list[QuoteWarning] = []
    trace: dict[str, Any] = {}
    if requested_timeline is None:
        return Decimal("1.00"), ceil_days(complexity_duration_days), "standard", False, warnings, trace
    requested_days = requested_timeline.to_business_days()
    schedule_ratio = complexity_duration_days / requested_days
    trace["requested_days"] = str(q2(requested_days))
    trace["schedule_ratio"] = str(q2(schedule_ratio))
    if schedule_ratio <= Decimal("0.85"):
        schedule_class = "relaxed"
        multiplier = max(
            policy.min_schedule_multiplier,
            Decimal("1") - policy.relax_slope * (Decimal("0.85") - schedule_ratio),
        )
        return q2(multiplier), ceil_days(requested_days), schedule_class, False, warnings, trace
    if schedule_ratio <= Decimal("1.00"):
        return Decimal("1.00"), ceil_days(requested_days), "standard", False, warnings, trace
    if schedule_ratio <= policy.max_compression_ratio:
        multiplier = min(
            policy.max_schedule_multiplier,
            Decimal("1") + policy.rush_slope * (schedule_ratio - Decimal("1")) * policy.service_beta,
        )
        warnings.append(
            QuoteWarning(
                code="RUSH_SCHEDULE",
                message="Requested delivery is faster than the standard calculated duration; rush premium applied.",
                service=service_config.service,
            )
        )
        return q2(multiplier), ceil_days(requested_days), "rush", False, warnings, trace
    min_feasible = complexity_duration_days / policy.max_compression_ratio
    warnings.append(
        QuoteWarning(
            code="TIMELINE_NOT_FEASIBLE_WITHOUT_REVIEW",
            message="Requested delivery exceeds the configured compression limit and requires human review.",
            service=service_config.service,
            requires_human_review=True,
        )
    )
    return (
        q2(policy.max_schedule_multiplier),
        ceil_days(min_feasible),
        "not_feasible_without_review",
        True,
        warnings,
        trace,
    )


def range_for_price(
    amount: Money,
    service_config: ServiceConfig,
    high_complexity: bool,
    confidence: float,
    human_review_required: bool,
) -> MoneyRange:
    policy = service_config.range_policy
    if human_review_required or high_complexity:
        low_width = policy.creative_high_complexity_low
        high_width = policy.creative_high_complexity_high
    elif confidence >= 0.9:
        low_width = policy.complete_low
        high_width = policy.complete_high
    else:
        low_width = policy.optional_unclear_low
        high_width = policy.optional_unclear_high
    return MoneyRange(low=amount * (Decimal("1") - low_width), high=amount * (Decimal("1") + high_width))


def human_review_for_value(service_config: ServiceConfig, price: Money) -> QuoteWarning | None:
    if price.amount >= service_config.human_review.high_value_threshold:
        return QuoteWarning(
            code="HIGH_VALUE_QUOTE",
            message="Quote value exceeds automatic approval threshold and requires human review.",
            service=service_config.service,
            requires_human_review=True,
        )
    return None
