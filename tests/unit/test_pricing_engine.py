from pathlib import Path

import pytest
from pydantic import ValidationError

from bookcraft.components.pricing import PricingQuoteRequest, PricingTimelineEngine, QuoteStatus
from bookcraft.components.pricing.config import load_engine_config, validate_engine_config
from bookcraft.components.pricing.models import ServiceCategory
from bookcraft.components.pricing.verifier import PricingVerifier

V2_DIR = Path("data/pricing/v2")


def _ghostwriting_request(words: int = 60000) -> PricingQuoteRequest:
    return PricingQuoteRequest.model_validate(
        {
            "requested_services": ["ghostwriting"],
            "service_inputs": {
                "ghostwriting": {
                    "service_type": "full_ghostwriting",
                    "category": "fiction_standard",
                    "word_count": words,
                    "manuscript_status": "outline_ready",
                }
            },
            "global_inputs": {"word_count": words},
        }
    )


def test_v2_config_validation_passes() -> None:
    result = validate_engine_config(load_engine_config(V2_DIR))
    assert result.valid is True
    assert result.errors == []


def test_v2_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PricingQuoteRequest.model_validate(
            {"requested_services": ["ghostwriting"], "unknown": "blocked"}
        )


def test_required_input_gate_returns_service_specific_questions() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    quote = engine.quote(
        PricingQuoteRequest.model_validate({"requested_services": ["ghostwriting"]})
    )

    assert quote.status == QuoteStatus.NEEDS_CLARIFICATION
    assert quote.missing_inputs
    assert "ghostwriting option" in quote.missing_inputs[0].question


def test_gated_values_block_customer_facing_numbers() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=False)
    quote = engine.quote(_ghostwriting_request())

    assert quote.status == QuoteStatus.HUMAN_REVIEW_REQUIRED
    assert quote.line_items == []
    assert any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings)


def test_approved_v2_values_return_deterministic_quote() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    quote = engine.quote(_ghostwriting_request())

    assert quote.status == QuoteStatus.ESTIMATED
    assert quote.line_items[0].base_price.amount > 0
    assert quote.total_price_range.low.amount <= quote.total_price_range.high.amount
    assert quote.timeline.total_timeline.low <= quote.timeline.total_timeline.high
    assert quote.assumptions


def test_quote_acceptance_only_from_allowed_status() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    quote = engine.quote(_ghostwriting_request())

    accepted = engine.accept_quote(quote.quote_id)

    assert accepted.status == QuoteStatus.ACCEPTED
    with pytest.raises(ValueError):
        engine.accept_quote(quote.quote_id)


def test_pricing_verifier_accepts_v2_config_without_value_approval() -> None:
    errors = PricingVerifier(strict=True, engine_version="v2").verify(V2_DIR)
    assert errors == []


def test_required_inputs_are_available_for_all_services() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    required = engine.list_required_inputs(list(ServiceCategory))

    assert set(required) == set(ServiceCategory)
    assert all(items for items in required.values())
