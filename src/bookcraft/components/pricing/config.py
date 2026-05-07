from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, field_validator, model_validator

from .models import ServiceCategory


class RateGrid(BaseModel):
    unit_type: str
    rates: dict[str, Any]
    minimum_fee: Decimal = Decimal("0")


class PackageGrid(BaseModel):
    rates: dict[str, Any]
    unit_type: str = "project"


class PaceGrid(BaseModel):
    unit_type: str
    pace: dict[str, Any] | Decimal
    quality_buffer_percent: dict[str, Any] | Decimal = Decimal("0")


class AddOnConfig(BaseModel):
    code: str
    title: str
    price_type: Literal["fixed", "per_unit", "quote_only"]
    price: Decimal | None = None
    unit_field: str | None = None
    duration_type: Literal["fixed_days", "per_unit_pace", "none", "quote_only"] = "none"
    duration_days: Decimal | None = None
    pace_per_day: Decimal | None = None
    complexity_points: Decimal = Decimal("0")
    human_review_required: bool = False

    @model_validator(mode="after")
    def validate_logic(self) -> AddOnConfig:
        if self.price_type in {"fixed", "per_unit"} and self.price is None:
            raise ValueError(f"Add-on {self.code} requires price")
        if self.price_type == "per_unit" and not self.unit_field:
            raise ValueError(f"Add-on {self.code} requires unit_field")
        if self.duration_type == "fixed_days" and self.duration_days is None:
            raise ValueError(f"Add-on {self.code} requires duration_days")
        if self.duration_type == "per_unit_pace" and (not self.unit_field or self.pace_per_day is None):
            raise ValueError(f"Add-on {self.code} requires unit_field and pace_per_day")
        if self.price_type == "quote_only" or self.duration_type == "quote_only":
            self.human_review_required = True
        return self


class ComplexityDriverOption(BaseModel):
    value: str
    points: Decimal
    reason: str | None = None
    group: str = "general"


class ComplexityDriver(BaseModel):
    field: str
    label: str
    options: list[ComplexityDriverOption]

    def option_for(self, selected: Any) -> ComplexityDriverOption | None:
        selected_norm = str(selected).strip().lower()
        for option in self.options:
            if option.value.strip().lower() == selected_norm:
                return option
        return None


class ComplexityModelConfig(BaseModel):
    factor_name: str
    mode: Literal["points", "ghostwriting_weighted"] = "points"
    point_multiplier: Decimal = Decimal("0.05")
    service_specific_point_multipliers: dict[str, Decimal] = Field(default_factory=dict)
    max_factor: Decimal = Decimal("1.60")
    weighted_coefficients: dict[str, Decimal] = Field(default_factory=dict)
    drivers: list[ComplexityDriver] = Field(default_factory=list)


class TimelineServiceMultiplierConfig(BaseModel):
    beta: Decimal = Decimal("1.00")
    min_tlf: Decimal | None = None
    max_tlf: Decimal | None = None


class TimelineTuningConfig(BaseModel):
    factor_name: str | None = None
    rush_slope: Decimal | None = None
    relax_slope: Decimal | None = None
    accelerated_slope: Decimal | None = None
    intensive_slope: Decimal | None = None
    min_tlf: Decimal | None = None
    max_tlf: Decimal | None = None
    service_multipliers: dict[str, TimelineServiceMultiplierConfig] = Field(default_factory=dict)
    campaign_duration_multipliers: dict[str, Decimal] = Field(default_factory=dict)


class TimelinePolicyConfig(BaseModel):
    max_compression_ratio: Decimal = Decimal("1.35")
    min_schedule_multiplier: Decimal = Decimal("0.95")
    max_schedule_multiplier: Decimal = Decimal("1.50")
    relax_slope: Decimal = Decimal("0.05")
    rush_slope: Decimal = Decimal("0.30")
    service_beta: Decimal = Decimal("1.00")


class RangePolicyConfig(BaseModel):
    complete_low: Decimal = Decimal("0.10")
    complete_high: Decimal = Decimal("0.10")
    optional_unclear_low: Decimal = Decimal("0.05")
    optional_unclear_high: Decimal = Decimal("0.20")
    creative_high_complexity_low: Decimal = Decimal("0.10")
    creative_high_complexity_high: Decimal = Decimal("0.25")


class RequiredInputConfig(BaseModel):
    field: str
    question: str
    service_input: bool = True
    fallback_global_field: str | None = None


class HumanReviewPolicyConfig(BaseModel):
    high_value_threshold: Decimal = Decimal("10000")
    marketing_ad_budget_threshold: Decimal = Decimal("5000")
    max_manual_discount_percent: Decimal = Decimal("10")
    custom_values_trigger_review: bool = True


class ServiceConfig(BaseModel):
    service: ServiceCategory
    version: str
    display_name: str
    currency: str = "USD"
    calculation_model: Literal[
        "word_rate",
        "page_rate",
        "package_grid",
        "package_plus_recurring",
        "per_finished_hour",
        "video_length_grid",
        "cover_illustration",
        "campaign_package",
    ]
    required_inputs: list[RequiredInputConfig]
    rate_grid: RateGrid | None = None
    package_grid: PackageGrid | None = None
    pace_grid: PaceGrid | None = None
    base_duration_days: dict[str, Any] | Decimal | None = None
    complexity: ComplexityModelConfig
    timeline_policy: TimelinePolicyConfig = Field(default_factory=TimelinePolicyConfig)
    timeline_tuning: TimelineTuningConfig | None = None
    range_policy: RangePolicyConfig = Field(default_factory=RangePolicyConfig)
    human_review: HumanReviewPolicyConfig = Field(default_factory=HumanReviewPolicyConfig)
    add_ons: list[AddOnConfig] = Field(default_factory=list)
    printing_cost_grid: dict[str, dict[str, Any]] = Field(default_factory=dict)
    enterprise_rollout_policy: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    source_reference: str | None = None

    @field_validator("version")
    @classmethod
    def nonempty_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("version cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_model_requirements(self) -> ServiceConfig:
        if self.calculation_model in {"word_rate", "page_rate", "per_finished_hour"} and not self.rate_grid:
            raise ValueError(f"{self.service} requires rate_grid")
        if self.calculation_model in {
            "package_grid",
            "package_plus_recurring",
            "video_length_grid",
            "campaign_package",
        } and not self.package_grid:
            raise ValueError(f"{self.service} requires package_grid")
        if not self.required_inputs:
            raise ValueError(f"{self.service} requires required_inputs")
        if self.complexity.max_factor < Decimal("1"):
            raise ValueError("max complexity factor must be >= 1")
        return self

    def addon_by_code(self, code: str) -> AddOnConfig | None:
        for addon in self.add_ons:
            if addon.code == code:
                return addon
        return None


class DiscountPolicy(BaseModel):
    version: str = "2.1"
    bundle_discounts: dict[str, Decimal] = Field(default_factory=dict)
    max_auto_discount_percent: Decimal = Decimal("20")


class PaymentSchedulePolicy(BaseModel):
    version: str = "2.1"
    options: dict[str, dict[str, Any]] = Field(default_factory=dict)


class QuotePolicy(BaseModel):
    version: str = "2.1"
    quote_expiration_days: int = 14
    confidence_complete: float = 0.90
    confidence_with_warnings: float = 0.75


class DependencyRule(BaseModel):
    after: list[ServiceCategory] = Field(default_factory=list)
    can_overlap_with: list[ServiceCategory] = Field(default_factory=list)
    can_start_if_manuscript_available: bool = False
    can_start_before_publication: bool = False


class DependencyGraph(BaseModel):
    version: str = "2.1"
    dependencies: dict[ServiceCategory, DependencyRule] = Field(default_factory=dict)


class EngineConfig(BaseModel):
    service_configs: dict[ServiceCategory, ServiceConfig]
    discount_policy: DiscountPolicy
    payment_schedule_policy: PaymentSchedulePolicy
    quote_policy: QuotePolicy
    dependency_graph: DependencyGraph

    @property
    def versions(self) -> dict[str, str]:
        versions: dict[str, str] = {
            "discount_policy": self.discount_policy.version,
            "payment_schedule_policy": self.payment_schedule_policy.version,
            "quote_policy": self.quote_policy.version,
            "dependency_graph": self.dependency_graph.version,
        }
        for service, config in self.service_configs.items():
            versions[f"service:{service.value}"] = config.version
        return versions


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML file must contain a mapping: {path}")
    return data


def load_engine_config(config_dir: str | Path) -> EngineConfig:
    root = Path(config_dir)
    service_dir = root / "service_configs"
    service_configs: dict[ServiceCategory, ServiceConfig] = {}
    if not service_dir.exists():
        raise FileNotFoundError(f"Missing service config directory: {service_dir}")
    for path in sorted(service_dir.glob("*.yaml")):
        if path.name.startswith("~$") or "__MACOSX" in path.parts:
            continue
        config = ServiceConfig.model_validate(_load_yaml(path))
        service_configs[config.service] = config
    if set(service_configs) != set(ServiceCategory):
        missing = set(ServiceCategory) - set(service_configs)
        extra = set(service_configs) - set(ServiceCategory)
        raise ValueError(f"Service config mismatch. Missing={missing}, Extra={extra}")
    return EngineConfig(
        service_configs=service_configs,
        discount_policy=DiscountPolicy.model_validate(_load_yaml(root / "discount_policy.v2.yaml")),
        payment_schedule_policy=PaymentSchedulePolicy.model_validate(
            _load_yaml(root / "payment_schedule_policy.v2.yaml")
        ),
        quote_policy=QuotePolicy.model_validate(_load_yaml(root / "quote_policy.v2.yaml")),
        dependency_graph=DependencyGraph.model_validate(_load_yaml(root / "dependency_graph.v2.yaml")),
    )


class ConfigValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def validate_engine_config(config: EngineConfig) -> ConfigValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    canonical_factor_names = {
        ServiceCategory.GHOSTWRITING: "GCF",
        ServiceCategory.EDITING_PROOFREADING: "ECF",
        ServiceCategory.COVER_DESIGN_ILLUSTRATION: "CCF",
        ServiceCategory.INTERIOR_FORMATTING: "FCF",
        ServiceCategory.PUBLISHING_DISTRIBUTION: "PCF",
        ServiceCategory.MARKETING_PROMOTION: "MCF",
        ServiceCategory.AUTHOR_WEBSITE: "WCF",
        ServiceCategory.AUDIOBOOK_PRODUCTION: "ACF",
        ServiceCategory.VIDEO_TRAILER: "VCF",
    }
    for service, service_config in config.service_configs.items():
        expected = canonical_factor_names[service]
        if service_config.complexity.factor_name != expected:
            errors.append(
                f"{service.value}: factor_name must be {expected}, got {service_config.complexity.factor_name}"
            )
        for addon in service_config.add_ons:
            if addon.price_type != "quote_only" and addon.price is not None and addon.price < 0:
                errors.append(f"{service.value}.{addon.code}: negative price")
            if addon.duration_days is not None and addon.duration_days < 0:
                errors.append(f"{service.value}.{addon.code}: negative duration")
        if service_config.human_review.high_value_threshold <= 0:
            warnings.append(f"{service.value}: high_value_threshold should be positive")
    return ConfigValidationResult(valid=not errors, errors=errors, warnings=warnings)
