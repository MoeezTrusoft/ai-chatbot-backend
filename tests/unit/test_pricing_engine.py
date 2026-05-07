from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from bookcraft.components.pricing import PricingQuoteRequest, PricingTimelineEngine
from bookcraft.components.pricing.schemas import (
    MoneyRange,
    PricingCalculationError,
    RequiredInputsRequest,
)
from bookcraft.components.pricing.verifier import PricingVerifier
from bookcraft.domain.enums import ServiceCategory

FIXTURE_DIR = Path("tests/fixtures/pricing")


def test_pricing_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PricingQuoteRequest.model_validate(
            {
                "service": "ghostwriting",
                "word_count": 50000,
                "genre": "fantasy",
                "thread_id": str(uuid4()),
                "raw_user_request": "quote",
                "unknown": "blocked",
            }
        )


def test_money_range_requires_high_at_least_low() -> None:
    with pytest.raises(ValidationError):
        MoneyRange(low=Decimal("10"), high=Decimal("9"))


def test_required_input_detection_for_all_services() -> None:
    engine = PricingTimelineEngine.from_rule_dir(FIXTURE_DIR, allow_placeholder_rules=True)
    for service in ServiceCategory:
        response = engine.list_required_inputs(RequiredInputsRequest(service=service))
        assert response.missing_inputs
        assert response.suggested_question


def test_production_placeholder_config_blocks_quote_numbers() -> None:
    engine = PricingTimelineEngine.from_rule_dir(
        Path("data/pricing"),
        allow_placeholder_rules=False,
    )
    response = engine.quote(
        PricingQuoteRequest(
            service=ServiceCategory.GHOSTWRITING,
            tier="standard",
            word_count=50000,
            genre="fantasy",
            thread_id=uuid4(),
            confidence=0.9,
            raw_user_request="quote",
        )
    )

    assert response.total_price_range is None
    assert response.human_review_required is True
    assert "pricing_rules_not_approved" in response.risk_flags


def test_fixture_config_returns_deterministic_range() -> None:
    engine = PricingTimelineEngine.from_rule_dir(FIXTURE_DIR, allow_placeholder_rules=True)
    response = engine.quote(
        PricingQuoteRequest(
            service=ServiceCategory.GHOSTWRITING,
            tier="standard",
            word_count=50000,
            genre="fantasy",
            thread_id=uuid4(),
            confidence=0.9,
            raw_user_request="quote",
        )
    )

    assert response.total_price_range is not None
    assert response.total_price_range.low == Decimal("9000.00")
    assert response.total_price_range.high == Decimal("11000.00")
    assert response.total_timeline_range is not None
    assert response.total_timeline_range.low <= response.total_timeline_range.high
    assert response.assumptions


def test_invalid_tier_fails_closed() -> None:
    engine = PricingTimelineEngine.from_rule_dir(FIXTURE_DIR, allow_placeholder_rules=True)
    with pytest.raises(PricingCalculationError):
        engine.quote(
            PricingQuoteRequest(
                service=ServiceCategory.GHOSTWRITING,
                tier="unsupported",
                word_count=50000,
                genre="fantasy",
                thread_id=uuid4(),
                confidence=0.9,
                raw_user_request="quote",
            )
        )


def test_human_review_flags_for_low_confidence_and_addons() -> None:
    engine = PricingTimelineEngine.from_rule_dir(FIXTURE_DIR, allow_placeholder_rules=True)
    response = engine.quote(
        PricingQuoteRequest(
            service=ServiceCategory.GHOSTWRITING,
            tier="standard",
            word_count=50000,
            genre="fantasy",
            add_ons=["extra interviews"],
            thread_id=uuid4(),
            confidence=0.4,
            raw_user_request="quote",
        )
    )

    assert response.human_review_required is True
    assert "add_ons_require_review" in response.risk_flags


def test_pricing_verifier_rejects_placeholders_by_default() -> None:
    with pytest.raises(ValueError):
        PricingVerifier(strict=True, allow_placeholders=False).verify(Path("data/pricing"))
