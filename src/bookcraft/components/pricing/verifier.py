from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bookcraft.components.pricing.config import load_engine_config, validate_engine_config
from bookcraft.components.pricing.rules import load_pricing_rules, verify_pricing_rules


@dataclass(frozen=True, slots=True)
class PricingVerifier:
    strict: bool = True
    allow_placeholders: bool = False
    values_approved: bool = False
    engine_version: str = "v2"

    def verify(self, rule_dir: Path) -> list[str]:
        if self.engine_version == "v2":
            errors = self._verify_v2(rule_dir)
        else:
            rule_set = load_pricing_rules(rule_dir)
            errors = verify_pricing_rules(rule_set, allow_placeholders=self.allow_placeholders)
        if self.strict and errors:
            msg = "Pricing verifier failed: " + "; ".join(errors)
            raise ValueError(msg)
        return errors

    def _verify_v2(self, rule_dir: Path) -> list[str]:
        config = load_engine_config(rule_dir)
        result = validate_engine_config(config)
        return list(result.errors)
