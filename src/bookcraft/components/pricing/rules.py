from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bookcraft.components.pricing.schemas import (
    PricingConfigurationError,
    ServiceCatalogConfig,
    ensure_decimal,
)
from bookcraft.domain.enums import ServiceCategory

APPROVED_FORMULAS = {
    "word_count_rate",
    "page_count_rate",
    "flat_project",
    "word_count_days",
    "page_count_days",
    "flat_days",
}
PLACEHOLDER = "REPLACE_WITH_APPROVED_VALUE"


class ServicePricingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formula: str
    rates: dict[str, object]

    @model_validator(mode="after")
    def approved_formula(self) -> ServicePricingRule:
        if self.formula not in APPROVED_FORMULAS:
            msg = f"unsupported pricing formula: {self.formula}"
            raise ValueError(msg)
        return self


class PricingRulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    currency: Literal["USD", "EUR", "GBP", "CAD", "AUD"] = "USD"
    rules: dict[ServiceCategory, ServicePricingRule]
    range_multiplier: dict[str, object] = Field(default_factory=dict)


class ServiceTimelineRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formula: str
    base_days: dict[str, object]

    @model_validator(mode="after")
    def approved_formula(self) -> ServiceTimelineRule:
        if self.formula not in APPROVED_FORMULAS:
            msg = f"unsupported timeline formula: {self.formula}"
            raise ValueError(msg)
        return self


class TimelineRulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    unit: Literal["business_days"] = "business_days"
    rules: dict[ServiceCategory, ServiceTimelineRule]
    range_padding_days: dict[str, object] = Field(default_factory=dict)


class HumanReviewPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    high_value_threshold: object = PLACEHOLDER


class PolicyRulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    quote_valid_days: int = Field(default=14, ge=1)
    human_review: HumanReviewPolicy = Field(default_factory=HumanReviewPolicy)
    assumptions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PricingRuleSet:
    catalog: ServiceCatalogConfig
    pricing: PricingRulesConfig
    timeline: TimelineRulesConfig
    policy: PolicyRulesConfig
    checksum: str
    has_placeholders: bool


def load_pricing_rules(rule_dir: Path) -> PricingRuleSet:
    files = {
        "catalog": rule_dir / "service_catalog.yaml",
        "pricing": rule_dir / "pricing_rules.yaml",
        "timeline": rule_dir / "timeline_rules.yaml",
        "policy": rule_dir / "policy_rules.yaml",
    }
    raw = {name: _load_yaml(path) for name, path in files.items()}
    serialized = "".join(path.read_text(encoding="utf-8") for path in files.values())
    return PricingRuleSet(
        catalog=ServiceCatalogConfig.model_validate(raw["catalog"]),
        pricing=PricingRulesConfig.model_validate(raw["pricing"]),
        timeline=TimelineRulesConfig.model_validate(raw["timeline"]),
        policy=PolicyRulesConfig.model_validate(raw["policy"]),
        checksum=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        has_placeholders=PLACEHOLDER in serialized,
    )


def verify_pricing_rules(rule_set: PricingRuleSet, *, allow_placeholders: bool) -> list[str]:
    errors: list[str] = []
    services = set(ServiceCategory)
    if set(rule_set.catalog.services) != services:
        errors.append("service_catalog.yaml must define all BookCraft services")
    if set(rule_set.pricing.rules) != services:
        errors.append("pricing_rules.yaml must define all BookCraft services")
    if set(rule_set.timeline.rules) != services:
        errors.append("timeline_rules.yaml must define all BookCraft services")
    if rule_set.has_placeholders and not allow_placeholders:
        errors.append("pricing rules contain REPLACE_WITH_APPROVED_VALUE placeholders")
    for service, entry in rule_set.catalog.services.items():
        pricing_rule = rule_set.pricing.rules.get(
            service,
            ServicePricingRule(formula="flat_project", rates={}),
        )
        timeline_rule = rule_set.timeline.rules.get(
            service,
            ServiceTimelineRule(formula="flat_days", base_days={}),
        )
        pricing_tiers = set(pricing_rule.rates)
        timeline_tiers = set(timeline_rule.base_days)
        expected = set(entry.tiers)
        if pricing_tiers != expected:
            errors.append(f"{service.value} pricing tiers do not match service catalog")
        if timeline_tiers != expected:
            errors.append(f"{service.value} timeline tiers do not match service catalog")
    if not allow_placeholders:
        _check_numeric_values(rule_set, errors)
    return errors


def _check_numeric_values(rule_set: PricingRuleSet, errors: list[str]) -> None:
    values: list[tuple[str, object]] = []
    for service, pricing_rule in rule_set.pricing.rules.items():
        values.extend(
            (f"pricing.{service.value}.{tier}", value) for tier, value in pricing_rule.rates.items()
        )
    for key, value in rule_set.pricing.range_multiplier.items():
        values.append((f"pricing.range_multiplier.{key}", value))
    for service, timeline_rule in rule_set.timeline.rules.items():
        values.extend(
            (f"timeline.{service.value}.{tier}", value)
            for tier, value in timeline_rule.base_days.items()
        )
    for key, value in rule_set.timeline.range_padding_days.items():
        values.append((f"timeline.range_padding_days.{key}", value))
    values.append(
        (
            "policy.human_review.high_value_threshold",
            rule_set.policy.human_review.high_value_threshold,
        )
    )
    for name, value in values:
        try:
            ensure_decimal(value, field_name=name)
        except PricingConfigurationError as exc:
            errors.append(str(exc))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PricingConfigurationError(f"missing pricing config file: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise PricingConfigurationError(f"{path} must contain a YAML object")
    return loaded
