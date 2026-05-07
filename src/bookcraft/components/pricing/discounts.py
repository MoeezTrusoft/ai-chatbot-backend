from __future__ import annotations

from decimal import Decimal

from .config import DiscountPolicy
from .models import DiscountLine, Money, PricingQuoteRequest, QuoteLineItem, ServiceCategory


def apply_discounts(
    request: PricingQuoteRequest,
    line_items: list[QuoteLineItem],
    policy: DiscountPolicy,
    currency: str = "USD",
) -> list[DiscountLine]:
    discounts: list[DiscountLine] = []
    subtotal = sum((item.final_price_range.low.amount + item.final_price_range.high.amount) / 2 for item in line_items)
    service_set = {ServiceCategory(item.service) for item in line_items}

    # Conservative bundle discounts: apply to midpoint estimate and return as a separate discount line.
    if {ServiceCategory.EDITING_PROOFREADING, ServiceCategory.INTERIOR_FORMATTING}.issubset(service_set):
        pct = policy.bundle_discounts.get("editing_formatting", Decimal("0"))
        if pct:
            discounts.append(
                DiscountLine(
                    code="BUNDLE_EDITING_FORMATTING",
                    description="Bundle discount for editing plus interior formatting.",
                    amount=Money(amount=subtotal * pct / Decimal("100"), currency=currency),
                    percent=pct,
                )
            )
    if {
        ServiceCategory.EDITING_PROOFREADING,
        ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        ServiceCategory.INTERIOR_FORMATTING,
        ServiceCategory.PUBLISHING_DISTRIBUTION,
    }.issubset(service_set):
        pct = policy.bundle_discounts.get("production_bundle", Decimal("0"))
        if pct:
            discounts.append(
                DiscountLine(
                    code="BUNDLE_PRODUCTION",
                    description="Production bundle discount for editing, cover, formatting, and publishing.",
                    amount=Money(amount=subtotal * pct / Decimal("100"), currency=currency),
                    percent=pct,
                )
            )

    if request.discount_request and request.discount_request.discount_type == "manual":
        pct = request.discount_request.requested_percent or Decimal("0")
        requires_review = pct > policy.max_auto_discount_percent
        discounts.append(
            DiscountLine(
                code="MANUAL_DISCOUNT_REQUEST",
                description=request.discount_request.reason or "Manual discount requested.",
                amount=Money(amount=subtotal * pct / Decimal("100"), currency=currency),
                percent=pct,
                requires_human_review=requires_review,
            )
        )
    return discounts
