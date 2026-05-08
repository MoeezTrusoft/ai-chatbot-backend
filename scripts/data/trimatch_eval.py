from __future__ import annotations

import json

from bookcraft.components.trimatch import RuleRepository, load_eval_examples
from bookcraft.components.trimatch.verifier import evaluate_rule_pack
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    rule_pack = RuleRepository(settings.trimatch_rule_dir).load_active_rules()
    examples = load_eval_examples(settings.trimatch_eval_dir)
    precision, recall = evaluate_rule_pack(rule_pack, examples)
    print(json.dumps({"precision": precision, "recall": recall}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
