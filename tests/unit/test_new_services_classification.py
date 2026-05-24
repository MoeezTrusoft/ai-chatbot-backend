"""Classification and routing tests for the 6 new BookCraft services.

Verifies deterministic service detection (keyword atoms), clarifying-options
coverage, consultation_only pricing config, and human-readable name mapping.
"""

from __future__ import annotations

import pytest

from bookcraft.components.preprocessor.embedding import EmbeddingClient
from bookcraft.components.preprocessor.processor import SharedPreprocessor
from bookcraft.components.preprocessor.sidecars import PreprocessorSidecars
from bookcraft.domain.enums import ServiceCategory

# ---------------------------------------------------------------------------
# Minimal preprocessor fixture (no live TEI server required)
# ---------------------------------------------------------------------------


class _StaticEmbeddingClient(EmbeddingClient):
    def __init__(self) -> None:
        super().__init__(
            tei_url="http://unused",
            timeout_seconds=0.1,
            dimensions=1,
            degraded_mode_enabled=True,
        )

    async def embed(self, normalized_text: str, language: str) -> list[float]:
        return [1.0]


@pytest.fixture
def preprocessor() -> SharedPreprocessor:
    return SharedPreprocessor(
        sidecars=PreprocessorSidecars(
            negation_cues=["no", "not", "without", "do not", "don't"],
            hedge_cues=["may", "might", "maybe", "could"],
            counterfactual_cues=["if", "would", "hypothetically"],
            typography_replacements={},
            compound_variants={},
        ),
        embedding_client=_StaticEmbeddingClient(),
    )


async def _services(preprocessor: SharedPreprocessor, text: str) -> list[str]:
    result = await preprocessor.process(text)
    value = result.deterministic_atoms.get("services")
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


# ---------------------------------------------------------------------------
# SERVICE_KEYWORDS detection — one representative phrase per service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_service"),
    [
        # fine_art_monograph
        ("I want to publish a coffee table book.", ServiceCategory.FINE_ART_MONOGRAPH),
        ("I'm working on an art monograph.", ServiceCategory.FINE_ART_MONOGRAPH),
        # catalog_transition
        (
            "My publisher is closing and I need to move my catalog.",
            ServiceCategory.CATALOG_TRANSITION,
        ),
        ("I want to get my rights back from my old publisher.", ServiceCategory.CATALOG_TRANSITION),
        # publishing_partnership
        ("I'm looking for a hybrid publishing partner.", ServiceCategory.PUBLISHING_PARTNERSHIP),
        ("Can you be my long term publisher?", ServiceCategory.PUBLISHING_PARTNERSHIP),
        # author_brand_platform
        ("I need help building my author platform.", ServiceCategory.AUTHOR_BRAND_PLATFORM),
        (
            "I want to grow my newsletter and author presence.",
            ServiceCategory.AUTHOR_BRAND_PLATFORM,
        ),
        # translation_foreign_rights
        ("I want to translate my book into Spanish.", ServiceCategory.TRANSLATION_FOREIGN_RIGHTS),
        ("What are my foreign rights options?", ServiceCategory.TRANSLATION_FOREIGN_RIGHTS),
        # special_collector_editions
        (
            "I want to create a limited edition of my book.",
            ServiceCategory.SPECIAL_COLLECTOR_EDITIONS,
        ),
        ("Can you make a signed collector edition?", ServiceCategory.SPECIAL_COLLECTOR_EDITIONS),
    ],
)
async def test_service_keyword_detection(
    preprocessor: SharedPreprocessor,
    text: str,
    expected_service: ServiceCategory,
) -> None:
    detected = await _services(preprocessor, text)
    assert expected_service.value in detected, (
        f"Expected {expected_service.value!r} in detected services for {text!r}, "
        f"got {detected}"
    )


# ---------------------------------------------------------------------------
# ServiceCategory enum completeness
# ---------------------------------------------------------------------------


def test_all_new_service_categories_in_enum() -> None:
    new_services = [
        "fine_art_monograph",
        "catalog_transition",
        "publishing_partnership",
        "author_brand_platform",
        "translation_foreign_rights",
        "special_collector_editions",
    ]
    enum_values = {s.value for s in ServiceCategory}
    for svc in new_services:
        assert svc in enum_values, f"ServiceCategory missing: {svc!r}"


# ---------------------------------------------------------------------------
# Clarifying options — all 6 new services must be in service_options
# ---------------------------------------------------------------------------


def test_new_services_in_clarifying_options() -> None:
    from bookcraft.components.sales.clarifying_options import ClarifyingOptionsBuilder

    builder = ClarifyingOptionsBuilder()
    result = builder.build("service_options")
    option_keys = {opt.key for opt in result.options}

    for svc in (
        "fine_art_monograph",
        "catalog_transition",
        "publishing_partnership",
        "author_brand_platform",
        "translation_foreign_rights",
        "special_collector_editions",
    ):
        assert svc in option_keys, f"service_options missing key: {svc!r}"


# ---------------------------------------------------------------------------
# Pricing config — consultation_only YAML files load without error
# ---------------------------------------------------------------------------


def test_new_service_pricing_configs_load() -> None:
    from bookcraft.components.pricing.config import load_engine_config

    engine = load_engine_config("data/pricing/v2")
    new_services = [
        ServiceCategory.FINE_ART_MONOGRAPH,
        ServiceCategory.CATALOG_TRANSITION,
        ServiceCategory.PUBLISHING_PARTNERSHIP,
        ServiceCategory.AUTHOR_BRAND_PLATFORM,
        ServiceCategory.TRANSLATION_FOREIGN_RIGHTS,
        ServiceCategory.SPECIAL_COLLECTOR_EDITIONS,
    ]
    for svc in new_services:
        cfg = engine.service_configs.get(svc)
        assert cfg is not None, f"Config not found for {svc}"
        assert cfg.calculation_model == "consultation_only", (
            f"{svc}: expected consultation_only, got {cfg.calculation_model}"
        )


# ---------------------------------------------------------------------------
# Pricing calculator — consultation_only returns human_review_required
# ---------------------------------------------------------------------------


def test_new_services_pricing_returns_consultation_warning() -> None:
    from bookcraft.components.pricing.calculators.service import calculate_service_line_item
    from bookcraft.components.pricing.config import load_engine_config
    from bookcraft.components.pricing.models import PricingQuoteRequest

    engine = load_engine_config("data/pricing/v2")
    new_services = [
        ServiceCategory.FINE_ART_MONOGRAPH,
        ServiceCategory.CATALOG_TRANSITION,
        ServiceCategory.PUBLISHING_PARTNERSHIP,
        ServiceCategory.AUTHOR_BRAND_PLATFORM,
        ServiceCategory.TRANSLATION_FOREIGN_RIGHTS,
        ServiceCategory.SPECIAL_COLLECTOR_EDITIONS,
    ]
    for svc in new_services:
        cfg = engine.service_configs[svc]
        request = PricingQuoteRequest(requested_services=[svc])
        line_item = calculate_service_line_item(
            service_config=cfg,
            request=request,
            service_inputs={},
        )
        assert line_item.human_review_required, f"{svc}: expected human_review_required=True"
        warning_codes = [w.code for w in line_item.warnings]
        assert "consultation_required" in warning_codes, (
            f"{svc}: expected consultation_required warning"
        )


# ---------------------------------------------------------------------------
# Response generator — human-readable names are mapped
# ---------------------------------------------------------------------------


def test_new_services_have_human_readable_names() -> None:
    from bookcraft.components.response.generator import _human_service_name

    for svc in (
        ServiceCategory.FINE_ART_MONOGRAPH,
        ServiceCategory.CATALOG_TRANSITION,
        ServiceCategory.PUBLISHING_PARTNERSHIP,
        ServiceCategory.AUTHOR_BRAND_PLATFORM,
        ServiceCategory.TRANSLATION_FOREIGN_RIGHTS,
        ServiceCategory.SPECIAL_COLLECTOR_EDITIONS,
    ):
        name = _human_service_name(svc.value)
        assert name and name != svc.value, (
            f"No human name mapped for {svc.value!r} (got {name!r})"
        )
