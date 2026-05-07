from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from bookcraft.components.pricing import PricingQuoteRequest, PricingTimelineEngine

V2_DIR = Path("data/pricing/v2")


def _ghostwriting(engine: PricingTimelineEngine, words: int, add_ons: list[str] | None = None):
    return engine.quote(
        PricingQuoteRequest.model_validate(
            {
                "requested_services": ["ghostwriting"],
                "service_inputs": {
                    "ghostwriting": {
                        "service_type": "full_ghostwriting",
                        "category": "fiction_standard",
                        "word_count": words,
                        "manuscript_status": "outline_ready",
                        "add_ons": add_ons or [],
                    }
                },
                "global_inputs": {"word_count": words},
            }
        )
    )


@given(
    low=st.integers(min_value=1000, max_value=50000),
    extra=st.integers(min_value=0, max_value=50000),
)
@settings(deadline=None)
def test_increasing_word_count_never_decreases_price(low: int, extra: int) -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)

    first = _ghostwriting(engine, low)
    second = _ghostwriting(engine, low + extra)

    assert second.total_price_range.low.amount >= first.total_price_range.low.amount
    assert second.total_price_range.high.amount >= first.total_price_range.high.amount


def test_addon_never_decreases_price() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)

    base = _ghostwriting(engine, 60000)
    addon = _ghostwriting(engine, 60000, ["outline_plan"])

    assert addon.total_price_range.low.amount >= base.total_price_range.low.amount


def test_discount_never_makes_total_negative() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    quote = engine.quote(
        PricingQuoteRequest.model_validate(
            {
                "requested_services": ["ghostwriting"],
                "service_inputs": {
                    "ghostwriting": {
                        "service_type": "full_ghostwriting",
                        "category": "fiction_standard",
                        "word_count": 60000,
                        "manuscript_status": "outline_ready",
                    }
                },
                "global_inputs": {"word_count": 60000},
                "discount_request": {
                    "discount_type": "manual",
                    "requested_percent": 1000,
                    "reason": "test",
                },
            }
        )
    )

    assert quote.total_price_range.low.amount >= 0
    assert quote.total_price_range.high.amount >= 0
