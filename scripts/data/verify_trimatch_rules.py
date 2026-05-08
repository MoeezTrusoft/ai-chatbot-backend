from __future__ import annotations

import json

from bookcraft.components.trimatch import RuleRepository, TriMatchVerifier, load_eval_examples
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    rule_pack = RuleRepository(settings.trimatch_rule_dir).load_active_rules()
    eval_examples = load_eval_examples(settings.trimatch_eval_dir)
    result = TriMatchVerifier().verify(rule_pack, eval_examples)
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if not result.valid:
        return 1
    print("trimatch verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
