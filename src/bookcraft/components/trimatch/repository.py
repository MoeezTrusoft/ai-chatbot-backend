from __future__ import annotations

import json
from pathlib import Path

from .schemas import RulePack, TriMatchRule


class RuleRepository:
    def __init__(self, rule_dir: str | Path) -> None:
        self.rule_dir = Path(rule_dir)

    def load_active_rules(self) -> RulePack:
        rules: list[TriMatchRule] = []
        versions: list[str] = []
        for path in sorted(self.rule_dir.glob("*.json")):
            loaded = json.loads(path.read_text(encoding="utf-8"))
            pack = RulePack.model_validate(loaded)
            versions.append(pack.version)
            rules.extend(rule for rule in pack.rules if rule.enabled)
        return RulePack(version="+".join(versions) or "empty", rules=rules)
