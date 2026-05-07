from pathlib import Path

from bookcraft.components.pricing import PricingQuoteRequest, PricingTimelineEngine
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    engine = PricingTimelineEngine.from_config_dir(
        Path(settings.pricing_v2_config_dir),
        values_approved=settings.pricing_v2_values_approved,
    )
    response = engine.quote(
        PricingQuoteRequest.model_validate(
            {
                "requested_services": ["ghostwriting"],
                "service_inputs": {
                    "ghostwriting": {
                        "service_type": "full_ghostwriting",
                        "category": "fiction_standard",
                        "word_count": 50000,
                        "manuscript_status": "outline_ready",
                    }
                },
                "global_inputs": {"word_count": 50000},
            }
        )
    )
    if not settings.pricing_v2_values_approved and response.line_items:
        raise RuntimeError("production placeholder config unexpectedly returned numbers")
    if settings.pricing_v2_values_approved:
        print(response.model_dump_json())
    else:
        print("pricing v2.1 values gated; no customer-facing numbers emitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
