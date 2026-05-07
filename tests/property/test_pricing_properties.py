from pathlib import Path
from uuid import uuid4

from hypothesis import given
from hypothesis import strategies as st

from bookcraft.components.pricing import PricingQuoteRequest, PricingTimelineEngine
from bookcraft.domain.enums import ServiceCategory

FIXTURE_DIR = Path("tests/fixtures/pricing")


@given(
    low=st.integers(min_value=1000, max_value=50000),
    extra=st.integers(min_value=0, max_value=50000),
)
def test_increasing_word_count_never_decreases_price(low: int, extra: int) -> None:
    engine = PricingTimelineEngine.from_rule_dir(FIXTURE_DIR, allow_placeholder_rules=True)

    first = engine.quote(
        PricingQuoteRequest(
            service=ServiceCategory.GHOSTWRITING,
            tier="standard",
            word_count=low,
            genre="fantasy",
            thread_id=uuid4(),
            confidence=0.9,
            raw_user_request="quote",
        )
    )
    second = engine.quote(
        PricingQuoteRequest(
            service=ServiceCategory.GHOSTWRITING,
            tier="standard",
            word_count=low + extra,
            genre="fantasy",
            thread_id=uuid4(),
            confidence=0.9,
            raw_user_request="quote",
        )
    )

    assert first.total_price_range is not None
    assert second.total_price_range is not None
    assert second.total_price_range.low >= first.total_price_range.low
    assert second.total_price_range.high >= first.total_price_range.high
