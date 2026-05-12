from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bookcraft.components.trimatch.schemas import RulePack

EXPECTED_RULE_FILES = {
    "service_intent_rules.v2.rules_army.json",
    "query_intent_rules.v2.rules_army.json",
    "funnel_stage_rules.v2.rules_army.json",
}


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/trimatch/staged/rules_army_v2")

    errors: list[str] = []
    warnings: list[str] = []
    total_rules = 0
    layer_counts: dict[str, int] = {}
    dimension_counts: dict[str, int] = {}
    shortcut_allowed = 0

    if not root.exists():
        print(json.dumps({"valid": False, "errors": [f"missing staged root: {root}"]}, indent=2))
        return 1

    rules_dir = root / "rules"
    found_rule_files = {path.name for path in rules_dir.glob("*.json")}

    missing = EXPECTED_RULE_FILES - found_rule_files
    extra = found_rule_files - EXPECTED_RULE_FILES

    for filename in sorted(missing):
        errors.append(f"missing expected rule file: {filename}")

    for filename in sorted(extra):
        warnings.append(f"unexpected rule file present: {filename}")

    seen_ids: set[str] = set()

    for path in sorted(rules_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pack = RulePack.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            errors.append(f"{path}: {exc}")
            continue

        for rule in pack.rules:
            total_rules += 1

            if rule.id in seen_ids:
                errors.append(f"duplicate rule id across packs: {rule.id}")
            seen_ids.add(rule.id)

            layer_counts[rule.layer.value] = layer_counts.get(rule.layer.value, 0) + 1
            dimension_counts[rule.target.dimension.value] = (
                dimension_counts.get(rule.target.dimension.value, 0) + 1
            )

            if rule.shortcut_allowed:
                shortcut_allowed += 1

            if rule.shortcut_allowed and rule.layer.value not in {"exact", "regex", "pattern"}:
                errors.append(f"{rule.id}: unsafe shortcut layer {rule.layer.value}")

    _require_file(root / "MANIFEST.json", errors)
    _require_file(root / "README.md", errors)
    _require_file(root / "sidecars" / "_negation_cues.v2.json", errors)
    _require_file(root / "sidecars" / "_compound_word_variants.v2.json", errors)
    _require_file(root / "sidecars" / "_semantic_clusters.v1.json", errors)
    _require_file(root / "sidecars" / "_context_rules.v1.json", errors)

    if total_rules < 900:
        errors.append(f"expected at least 900 rules, found {total_rules}")

    result: dict[str, Any] = {
        "valid": not errors,
        "staged_root": str(root),
        "total_rules": total_rules,
        "layer_counts": layer_counts,
        "dimension_counts": dimension_counts,
        "shortcut_allowed_rules": shortcut_allowed,
        "errors": errors,
        "warnings": warnings,
    }

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _require_file(path: Path, errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"missing required file: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
