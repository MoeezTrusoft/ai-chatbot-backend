from pathlib import Path

from bookcraft.components.pricing.verifier import PricingVerifier
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    errors = PricingVerifier(
        strict=settings.pricing_strict_verifier,
        allow_placeholders=settings.pricing_allow_placeholder_rules,
        values_approved=settings.pricing_v2_values_approved,
        engine_version=settings.pricing_engine_version,
    ).verify(Path(settings.pricing_v2_config_dir))
    if errors:
        print("pricing verifier warnings:")
        for error in errors:
            print(f"- {error}")
    else:
        print("pricing verifier passed")
    if not settings.pricing_v2_values_approved:
        print("pricing v2.1 values are installed but not approved for customer-facing use")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
