from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from ..config import ServiceConfig
from ..kernel import (
    ceil_days,
    compute_addons,
    compute_complexity,
    compute_schedule_multiplier,
    d,
    human_review_for_value,
    money,
    nested_lookup,
    q2,
    range_for_price,
)
from ..models import Money, MoneyRange, PricingQuoteRequest, QuoteLineItem, QuoteWarning, UnitType


class CalculationContext(BaseModel):
    service_config: ServiceConfig
    service_inputs: dict[str, Any]
    global_inputs: dict[str, Any]
    requested_timeline: Any = None


def calculate_service_line_item(
    service_config: ServiceConfig,
    request: PricingQuoteRequest,
    service_inputs: dict[str, Any],
) -> QuoteLineItem:
    global_inputs = request.global_inputs.model_dump()
    if service_config.calculation_model == "consultation_only":
        zero = money(Decimal("0"), service_config.currency)
        return QuoteLineItem(
            service=service_config.service,
            unit_type=UnitType.PROJECT.value,
            unit_quantity=Decimal("1"),
            base_price=zero,
            complexity_factor=Decimal("1"),
            complexity_price=zero,
            schedule_multiplier=Decimal("1"),
            rush_surcharge=zero,
            add_on_total=zero,
            final_price_range=MoneyRange(low=zero, high=zero),
            base_duration_days=Decimal("0"),
            complexity_duration_days=Decimal("0"),
            final_duration_days=Decimal("0"),
            assumptions=list(service_config.assumptions),
            warnings=[
                QuoteWarning(
                    code="consultation_required",
                    message=(
                        "This service is scoped on a consultation call. "
                        "Pricing is confirmed against final specification — no list price applies."
                    ),
                    service=service_config.service,
                    requires_human_review=True,
                )
            ],
            human_review_required=True,
            calculation_trace={
                "model": "consultation_only",
                "service": service_config.service.value,
            },
        )
    base_price, unit_type, unit_quantity, package_label, base_duration_days, trace = (
        _calculate_base(service_config, service_inputs, global_inputs)
    )
    complexity_factor, complexity_breakdown, group_points = compute_complexity(
        service_config, service_inputs
    )
    add_on_lines, addon_total, addon_days, addon_cp, addon_warnings = compute_addons(
        service_config, service_inputs, global_inputs
    )
    if addon_cp:
        complexity_factor = min(
            q2(complexity_factor + addon_cp * service_config.complexity.point_multiplier),
            service_config.complexity.max_factor,
        )
    complexity_price = base_price * complexity_factor
    complexity_duration_days = q2((base_duration_days * complexity_factor) + addon_days)
    (
        schedule_multiplier,
        final_duration_days,
        schedule_class,
        review_for_timeline,
        schedule_warnings,
        schedule_trace,
    ) = compute_schedule_multiplier(
        service_config, complexity_duration_days, service_inputs, request.requested_timeline
    )
    rush_surcharge = complexity_price * (schedule_multiplier - Decimal("1"))
    final_price_before_range = complexity_price + rush_surcharge + addon_total
    warnings = [*addon_warnings, *schedule_warnings]
    value_warning = human_review_for_value(service_config, final_price_before_range)
    if value_warning is not None:
        warnings.append(value_warning)
    custom_warning = _custom_value_warnings(service_config, service_inputs)
    warnings.extend(custom_warning)
    human_review_required = review_for_timeline or any(w.requires_human_review for w in warnings)
    confidence = 0.75 if warnings else 0.90
    high_complexity = complexity_factor >= Decimal("1.30")
    final_range = range_for_price(
        final_price_before_range, service_config, high_complexity, confidence, human_review_required
    )
    trace.update(
        {
            "complexity_factor": str(complexity_factor),
            "complexity_group_points": {k: str(v) for k, v in group_points.items()},
            "add_on_total": str(addon_total.amount),
            "add_on_days": str(addon_days),
            "schedule_class": schedule_class,
            "schedule_multiplier": str(schedule_multiplier),
            "schedule_trace": schedule_trace,
            "final_price_before_range": str(final_price_before_range.amount),
        }
    )
    assumptions = list(service_config.assumptions)
    if schedule_class == "rush":
        assumptions.append(
            "Timeline uses rush-compression pricing because the requested delivery is faster than standard duration."
        )
    if service_config.service.value == "author_website":
        assumptions.append(
            "Recurring monthly hosting/maintenance is returned separately from one-time setup where configured."
        )
    if service_config.service.value == "marketing_promotion":
        assumptions.append(
            "Ad spend is excluded from BookCraft service fees unless explicitly selected and approved."
        )

    return QuoteLineItem(
        service=service_config.service,
        package_or_tier=package_label,
        unit_type=unit_type,
        unit_quantity=q2(unit_quantity),
        base_price=base_price,
        complexity_factor=complexity_factor,
        complexity_price=complexity_price,
        schedule_multiplier=schedule_multiplier,
        rush_surcharge=rush_surcharge,
        add_on_total=addon_total,
        final_price_range=final_range,
        base_duration_days=q2(base_duration_days),
        complexity_duration_days=complexity_duration_days,
        final_duration_days=final_duration_days,
        selected_add_ons=add_on_lines,
        complexity_breakdown=complexity_breakdown,
        assumptions=assumptions,
        warnings=warnings,
        human_review_required=human_review_required,
        calculation_trace=trace,
    )


def _calculate_base(
    service_config: ServiceConfig,
    service_inputs: dict[str, Any],
    global_inputs: dict[str, Any],
) -> tuple[Money, str, Decimal, str | None, Decimal, dict[str, Any]]:
    model = service_config.calculation_model
    trace: dict[str, Any] = {"model": model, "service": service_config.service.value}
    if model == "word_rate":
        word_count = d(service_inputs.get("word_count") or global_inputs.get("word_count"))
        service_type = str(service_inputs.get("service_type"))
        category = str(service_inputs.get("category") or global_inputs.get("genre") or "standard")
        rate = d(nested_lookup(service_config.rate_grid.rates, [category, service_type]))  # type: ignore[union-attr]
        minimum = service_config.rate_grid.minimum_fee if service_config.rate_grid else Decimal("0")
        base_amount = max(word_count * rate, minimum)
        base_duration = _pace_duration(service_config, service_type, word_count)
        trace.update({"word_count": str(word_count), "rate": str(rate), "minimum": str(minimum)})
        return (
            money(base_amount, service_config.currency),
            UnitType.WORDS.value,
            word_count,
            service_type,
            base_duration,
            trace,
        )
    if model == "page_rate":
        page_count = d(service_inputs.get("page_count") or global_inputs.get("page_count"))
        output_format = str(
            service_inputs.get("output_format") or service_inputs.get("service_type")
        )
        category = str(service_inputs.get("category") or global_inputs.get("genre") or "fiction")
        rate = d(nested_lookup(service_config.rate_grid.rates, [category, output_format]))  # type: ignore[union-attr]
        minimum = service_config.rate_grid.minimum_fee if service_config.rate_grid else Decimal("0")
        base_amount = max(page_count * rate, minimum)
        base_duration = _pace_duration(service_config, output_format, page_count, category=category)
        trace.update({"page_count": str(page_count), "rate": str(rate), "minimum": str(minimum)})
        return (
            money(base_amount, service_config.currency),
            UnitType.PAGES.value,
            page_count,
            output_format,
            base_duration,
            trace,
        )
    if model == "per_finished_hour":
        hours = _finished_hours(service_inputs, global_inputs)
        tier = str(service_inputs.get("tier"))
        rate = d(nested_lookup(service_config.rate_grid.rates, [tier]))  # type: ignore[union-attr]
        base_amount = hours * rate
        base_duration = _duration_from_config(service_config, [tier])
        trace.update({"finished_hours": str(hours), "rate": str(rate)})
        return (
            money(base_amount, service_config.currency),
            UnitType.FINISHED_HOURS.value,
            hours,
            tier,
            base_duration,
            trace,
        )
    if model == "package_grid":
        tier = str(service_inputs.get("tier"))
        dimension = str(
            service_inputs.get("package_dimension")
            or service_inputs.get("format")
            or service_inputs.get("website_type")
        )
        amount = d(nested_lookup(service_config.package_grid.rates, [tier, dimension]))  # type: ignore[union-attr]
        printing_cost = _printing_cost(service_config, service_inputs, trace)
        amount += printing_cost
        base_duration = _duration_from_config(service_config, [tier, dimension])
        return (
            money(amount, service_config.currency),
            UnitType.PROJECT.value,
            Decimal("1"),
            f"{tier}/{dimension}",
            base_duration,
            trace,
        )
    if model == "package_plus_recurring":
        tier = str(service_inputs.get("tier"))
        amount = d(nested_lookup(service_config.package_grid.rates, [tier, "setup"]))  # type: ignore[union-attr]
        included_pages = d(
            nested_lookup(service_config.package_grid.rates, [tier, "pages_included"])  # type: ignore[union-attr]
        )
        requested_pages = d(service_inputs.get("page_count") or included_pages)
        extra_pages = max(Decimal("0"), requested_pages - included_pages)
        extra_page_rate = d(
            nested_lookup(service_config.package_grid.rates, [tier, "additional_page"])  # type: ignore[union-attr]
        )
        amount += extra_pages * extra_page_rate
        recurring = d(nested_lookup(service_config.package_grid.rates, [tier, "monthly_recurring"]))  # type: ignore[union-attr]
        base_duration = _duration_from_config(service_config, [tier])
        trace.update({"monthly_recurring_usd": str(recurring), "extra_pages": str(extra_pages)})
        return (
            money(amount, service_config.currency),
            UnitType.PROJECT.value,
            Decimal("1"),
            tier,
            base_duration,
            trace,
        )
    if model == "video_length_grid":
        tier = str(service_inputs.get("tier"))
        length = str(service_inputs.get("video_length_seconds"))
        amount = d(nested_lookup(service_config.package_grid.rates, [tier, length]))  # type: ignore[union-attr]
        base_duration = _duration_from_config(service_config, [tier])
        return (
            money(amount, service_config.currency),
            UnitType.VIDEO_SECONDS.value,
            d(length),
            tier,
            base_duration,
            trace,
        )
    if model == "campaign_package":
        tier = str(service_inputs.get("tier"))
        duration = str(service_inputs.get("campaign_duration"))
        if tier not in (service_config.package_grid.rates if service_config.package_grid else {}):
            if service_config.enterprise_rollout_policy.get("pricing_mode") == "quote_only":
                trace.update(
                    {
                        "quote_only_tier": tier,
                        "quote_only_reason": service_config.enterprise_rollout_policy.get("reason"),
                    }
                )
                amount = Decimal("0")
            else:
                raise KeyError(f"Campaign tier {tier!r} not found")
        else:
            amount = d(nested_lookup(service_config.package_grid.rates, [tier, duration]))  # type: ignore[union-attr]
        setup_days = _duration_from_config(service_config, [tier])
        active_days = _campaign_duration_days(duration)
        trace.update({"campaign_active_calendar_days": str(active_days)})
        return (
            money(amount, service_config.currency),
            UnitType.CALENDAR_DAYS.value,
            active_days,
            f"{tier}/{duration}",
            setup_days,
            trace,
        )
    if model == "cover_illustration":
        return _cover_base(service_config, service_inputs, trace)
    # consultation_only is handled above in calculate_service_line_item before _calculate_base is called
    raise ValueError(f"Unsupported calculation_model: {model}")


def _printing_cost(
    service_config: ServiceConfig,
    service_inputs: dict[str, Any],
    trace: dict[str, Any],
) -> Decimal:
    if not service_config.printing_cost_grid:
        return Decimal("0")
    trim_size = service_inputs.get("trim_size")
    print_type = service_inputs.get("print_type")
    if trim_size is None or print_type is None:
        return Decimal("0")
    grid_value = nested_lookup(service_config.printing_cost_grid, [str(trim_size), str(print_type)])
    if isinstance(grid_value, str) and grid_value.strip().lower() == "quote_only":
        trace.update(
            {
                "printing_cost_requires_review": True,
                "printing_trim_size": str(trim_size),
                "printing_print_type": str(print_type),
            }
        )
        return Decimal("0")
    quantity = d(service_inputs.get("print_quantity") or 1)
    unit_cost = d(grid_value)
    total = unit_cost * quantity
    trace.update(
        {
            "printing_cost_unit": str(unit_cost),
            "printing_cost_quantity": str(quantity),
            "printing_cost_total": str(total),
        }
    )
    return total


def _pace_duration(
    service_config: ServiceConfig,
    key: str,
    units: Decimal,
    category: str | None = None,
) -> Decimal:
    if service_config.pace_grid is None:
        return Decimal("0")
    pace_mapping = service_config.pace_grid.pace
    if category is not None and isinstance(pace_mapping, dict):
        pace = d(nested_lookup(pace_mapping, [category, key]))
    elif isinstance(pace_mapping, dict):
        pace = d(nested_lookup(pace_mapping, [key]))
    else:
        pace = d(pace_mapping)
    duration = units / pace
    buffer_mapping = service_config.pace_grid.quality_buffer_percent
    if category is not None and isinstance(buffer_mapping, dict):
        buffer_percent = d(nested_lookup(buffer_mapping, [category, key]))
    elif isinstance(buffer_mapping, dict):
        buffer_percent = d(nested_lookup(buffer_mapping, [key]))
    else:
        buffer_percent = d(buffer_mapping)
    return ceil_days(duration * (Decimal("1") + buffer_percent / Decimal("100")))


def _duration_from_config(service_config: ServiceConfig, path: list[str]) -> Decimal:
    value = service_config.base_duration_days
    if value is None:
        return Decimal("0")
    if isinstance(value, dict):
        return ceil_days(d(nested_lookup(value, path)))
    return ceil_days(d(value))


def _finished_hours(service_inputs: dict[str, Any], global_inputs: dict[str, Any]) -> Decimal:
    if service_inputs.get("finished_hours") is not None:
        return d(service_inputs["finished_hours"])
    words = d(service_inputs.get("word_count") or global_inputs.get("word_count"))
    words_per_finished_hour = d(service_inputs.get("words_per_finished_hour") or 9300)
    return q2(words / words_per_finished_hour)


def _campaign_duration_days(duration_label: str) -> Decimal:
    label = duration_label.strip().lower()
    if "12" in label:
        return Decimal("365")
    if "6" in label:
        return Decimal("182")
    if "3" in label:
        return Decimal("91")
    return Decimal("30")


def _cover_base(
    service_config: ServiceConfig,
    service_inputs: dict[str, Any],
    trace: dict[str, Any],
) -> tuple[Money, str, Decimal, str | None, Decimal, dict[str, Any]]:
    cover_amount = Decimal("0")
    duration = Decimal("0")
    package_label_parts: list[str] = []
    if service_inputs.get("cover_type"):
        cover_type = str(service_inputs["cover_type"])
        complexity_level = str(service_inputs.get("complexity_level", "standard"))
        cover_amount += d(
            nested_lookup(
                service_config.package_grid.rates,  # type: ignore[union-attr]
                ["covers", cover_type, complexity_level],
            )
        )
        duration += d(
            nested_lookup(
                service_config.base_duration_days, ["covers", cover_type, complexity_level]
            )
        )
        package_label_parts.append(f"{cover_type}/{complexity_level}")
    illustration_count = d(service_inputs.get("illustration_count") or 0)
    if illustration_count:
        color_mode = str(service_inputs.get("color_mode", "color"))
        illustration_type = str(service_inputs.get("illustration_type", "full_page"))
        illustration_rate = d(
            nested_lookup(
                service_config.package_grid.rates,  # type: ignore[union-attr]
                ["illustrations", color_mode, illustration_type],
            )
        )
        cover_amount += illustration_count * illustration_rate
        duration += illustration_count * Decimal("1.5")
        package_label_parts.append(
            f"{illustration_count} {color_mode} {illustration_type} illustrations"
        )
    if not package_label_parts:
        raise KeyError("cover_type or illustration_count is required")
    trace["cover_illustration_amount"] = str(cover_amount)
    return (
        money(cover_amount, service_config.currency),
        UnitType.ASSETS.value,
        max(Decimal("1"), illustration_count),
        " + ".join(package_label_parts),
        ceil_days(duration),
        trace,
    )


def _custom_value_warnings(
    service_config: ServiceConfig, service_inputs: dict[str, Any]
) -> list[QuoteWarning]:
    warnings: list[QuoteWarning] = []
    if service_config.human_review.custom_values_trigger_review:
        for key, value in service_inputs.items():
            if isinstance(value, str) and value.strip().lower() in {
                "custom",
                "quote_only",
                "enterprise",
            }:
                warnings.append(
                    QuoteWarning(
                        code="CUSTOM_VALUE_REQUIRES_REVIEW",
                        message=f"Custom value for {key} requires human review.",
                        service=service_config.service,
                        requires_human_review=True,
                    )
                )
    if service_config.service.value == "marketing_promotion":
        tier = str(service_inputs.get("tier", "")).strip().lower()
        if tier == "enterprise_rollout" and service_config.enterprise_rollout_policy:
            warnings.append(
                QuoteWarning(
                    code="ENTERPRISE_ROLLOUT_REQUIRES_REVIEW",
                    message="Enterprise rollout pricing is configured as quote-only and requires human review.",
                    service=service_config.service,
                    requires_human_review=True,
                )
            )
        ad_budget = service_inputs.get("ad_budget")
        if (
            ad_budget is not None
            and d(ad_budget) >= service_config.human_review.marketing_ad_budget_threshold
        ):
            warnings.append(
                QuoteWarning(
                    code="AD_BUDGET_REQUIRES_REVIEW",
                    message="Requested ad budget exceeds auto-approval threshold and requires human review.",
                    service=service_config.service,
                    requires_human_review=True,
                )
            )
    if service_config.printing_cost_grid:
        trim_size = service_inputs.get("trim_size")
        print_type = service_inputs.get("print_type")
        if trim_size is not None and print_type is not None:
            grid_value = nested_lookup(
                service_config.printing_cost_grid, [str(trim_size), str(print_type)]
            )
            if isinstance(grid_value, str) and grid_value.strip().lower() == "quote_only":
                warnings.append(
                    QuoteWarning(
                        code="PRINTING_COST_REQUIRES_REVIEW",
                        message="Selected print production cost is quote-only and requires human review.",
                        service=service_config.service,
                        requires_human_review=True,
                    )
                )
    return warnings
