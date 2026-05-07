from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bookcraft.components.pricing.rules import load_pricing_rules, verify_pricing_rules


@dataclass(frozen=True, slots=True)
class PricingVerifier:
    strict: bool = True
    allow_placeholders: bool = False

    def verify(self, rule_dir: Path) -> list[str]:
        rule_set = load_pricing_rules(rule_dir)
        errors = verify_pricing_rules(rule_set, allow_placeholders=self.allow_placeholders)
        if self.strict and errors:
            msg = "Pricing verifier failed: " + "; ".join(errors)
            raise ValueError(msg)
        return errors
