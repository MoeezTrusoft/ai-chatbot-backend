from pathlib import Path

import pytest
from pydantic import ValidationError

from bookcraft.components.pricing import PricingQuoteRequest, PricingTimelineEngine, QuoteStatus
from bookcraft.components.pricing.config import load_engine_config, validate_engine_config
from bookcraft.components.pricing.kernel import compute_complexity
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
    from bookcraft.components.pricing.config import load_engine_config

    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    required = engine.list_required_inputs(list(ServiceCategory))
    cfg = load_engine_config(V2_DIR)

    assert set(required) == set(ServiceCategory)
    # consultation_only services have no required inputs by design — they route
    # straight to human review rather than collecting parameters.
    non_consultation = [
        svc for svc in ServiceCategory
        if cfg.service_configs[svc].calculation_model != "consultation_only"
    ]
    assert all(required[svc] for svc in non_consultation)


def test_v2_2_editing_service_specific_complexity_multipliers_apply() -> None:
    config = load_engine_config(V2_DIR)
    service_config = config.service_configs[ServiceCategory.EDITING_PROOFREADING]

    developmental_factor, _, _ = compute_complexity(
        service_config,
        {"service_type": "developmental_editing", "manuscript_condition": "very_rough"},
    )
    copy_factor, _, _ = compute_complexity(
        service_config,
        {"service_type": "copy_editing", "manuscript_condition": "very_rough"},
    )

    assert developmental_factor > copy_factor


def test_v2_2_publishing_printing_cost_grid_adds_to_base_price() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    base_request = {
        "requested_services": ["publishing_distribution"],
        "service_inputs": {
            "publishing_distribution": {
                "tier": "essential",
                "package_dimension": "print_only",
                "distribution_channels": "print_only",
            }
        },
    }
    quote_without_printing = engine.quote(PricingQuoteRequest.model_validate(base_request))

    with_printing = base_request | {
        "service_inputs": {
            "publishing_distribution": {
                "tier": "essential",
                "package_dimension": "print_only",
                "distribution_channels": "print_only",
                "trim_size": "6x9",
                "print_type": "paperback_bw",
                "print_quantity": 10,
            }
        }
    }
    quote_with_printing = engine.quote(PricingQuoteRequest.model_validate(with_printing))
    item = quote_with_printing.line_items[0]

    assert item.base_price.amount > quote_without_printing.line_items[0].base_price.amount
    assert item.calculation_trace["printing_cost_total"] == "42.50"


def test_v2_2_publishing_quote_only_printing_cost_requires_review() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    quote = engine.quote(
        PricingQuoteRequest.model_validate(
            {
                "requested_services": ["publishing_distribution"],
                "service_inputs": {
                    "publishing_distribution": {
                        "tier": "essential",
                        "package_dimension": "print_only",
                        "distribution_channels": "print_only",
                        "trim_size": "custom_size",
                        "print_type": "paperback_bw",
                    }
                },
            }
        )
    )

    assert quote.status == QuoteStatus.HUMAN_REVIEW_REQUIRED
    assert any(warning.code == "PRINTING_COST_REQUIRES_REVIEW" for warning in quote.warnings)


def test_v2_2_marketing_enterprise_rollout_is_quote_only_review() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    quote = engine.quote(
        PricingQuoteRequest.model_validate(
            {
                "requested_services": ["marketing_promotion"],
                "service_inputs": {
                    "marketing_promotion": {
                        "tier": "enterprise_rollout",
                        "campaign_duration": "3_months",
                        "primary_goal": "launch_support",
                    }
                },
            }
        )
    )

    assert quote.status == QuoteStatus.HUMAN_REVIEW_REQUIRED
    assert quote.line_items[0].calculation_trace["quote_only_tier"] == "enterprise_rollout"
    assert any(warning.code == "ENTERPRISE_ROLLOUT_REQUIRES_REVIEW" for warning in quote.warnings)


def test_v2_2_marketing_campaign_duration_tuning_is_traced() -> None:
    engine = PricingTimelineEngine.from_config_dir(V2_DIR, values_approved=True)
    quote = engine.quote(
        PricingQuoteRequest.model_validate(
            {
                "requested_services": ["marketing_promotion"],
                "service_inputs": {
                    "marketing_promotion": {
                        "tier": "professional_campaign",
                        "campaign_duration": "1_month",
                        "primary_goal": "awareness",
                    }
                },
            }
        )
    )

    schedule_trace = quote.line_items[0].calculation_trace["schedule_trace"]
    assert schedule_trace["timeline_tuning_factor"] == "MTLF"
    assert schedule_trace["timeline_tuning_key"] == "professional_campaign"
    assert schedule_trace["campaign_duration_multiplier"] == "1.2"
