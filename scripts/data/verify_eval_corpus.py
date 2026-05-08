from __future__ import annotations

import json
from pathlib import Path

from bookcraft.components.trimatch.schemas import EvalExample


def main() -> int:
    errors: list[str] = []
    count = 0
    for path in sorted(Path("data/trimatch/eval").glob("*.jsonl")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                EvalExample.model_validate(json.loads(line))
            except Exception as exc:  # noqa: BLE001 - verifier should report all schema failures.
                errors.append(f"{path}:{line_number}: {exc}")
            count += 1
    if count == 0:
        errors.append("no eval examples found")
    if errors:
        print("eval corpus verifier failed")
        for error in errors:
            print(error)
        return 1
    print(f"eval corpus verifier passed: {count} examples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
