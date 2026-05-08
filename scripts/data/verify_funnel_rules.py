from __future__ import annotations

import json

from bookcraft.components.funnel_signal import partition_source, verify_funnel_partition
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    report, rule_pack = partition_source(settings.funnel_rule_source_path)
    errors = verify_funnel_partition(report, rule_pack)
    payload = {
        "valid": not errors,
        "errors": errors,
        "source_version": report.source_version,
        "user_language_count": report.user_language_count,
        "crm_count": report.crm_count,
        "dropped_count": report.dropped_count,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if errors:
        return 1
    print("funnel verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
