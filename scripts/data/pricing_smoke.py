from pathlib import Path
from uuid import uuid4

from bookcraft.components.pricing import PricingQuoteRequest, PricingTimelineEngine
from bookcraft.domain.enums import ServiceCategory
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    engine = PricingTimelineEngine.from_rule_dir(
        Path(settings.pricing_rule_dir),
        allow_placeholder_rules=settings.pricing_allow_placeholder_rules,
    )
    response = engine.quote(
        PricingQuoteRequest(
            service=ServiceCategory.GHOSTWRITING,
            tier="standard",
            word_count=50000,
            genre="fantasy",
            thread_id=uuid4(),
            confidence=0.9,
            raw_user_request="smoke quote",
        )
    )
    if response.total_price_range is not None and not settings.pricing_allow_placeholder_rules:
        raise RuntimeError("production placeholder config unexpectedly returned numbers")
    print(response.suggested_phrasing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
