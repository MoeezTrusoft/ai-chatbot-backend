from __future__ import annotations

import json

from bookcraft.components.funnel_signal import FunnelRulePartitioner, load_funnel_source
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    version, rules = load_funnel_source(settings.funnel_rule_source_path)
    partitioner = FunnelRulePartitioner()
    report = partitioner.partition(rules, source_version=version)
    rule_pack = partitioner.to_trimatch_rule_pack(report)
    payload = {
        "report": report.model_dump(mode="json"),
        "trimatch_rule_pack": rule_pack.model_dump(mode="json"),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
