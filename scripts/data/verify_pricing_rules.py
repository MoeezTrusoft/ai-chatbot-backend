from pathlib import Path

from bookcraft.components.pricing.verifier import PricingVerifier
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    errors = PricingVerifier(
        strict=settings.pricing_strict_verifier,
        allow_placeholders=settings.pricing_allow_placeholder_rules,
    ).verify(Path(settings.pricing_rule_dir))
    if errors:
        print("pricing verifier warnings:")
        for error in errors:
            print(f"- {error}")
    else:
        print("pricing verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
