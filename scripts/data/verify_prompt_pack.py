from __future__ import annotations

import re
from pathlib import Path

FORBIDDEN_PATTERNS = {
    "exact_price": re.compile(r"\$\s?\d+", re.I),
    "timeline_number": re.compile(r"\b\d+\s?(business days|days|weeks|months)\b", re.I),
    "legal_clause_generation": re.compile(
        r"\b(write|draft|compose)\b.*\b(legal|clause|contract)\b",
        re.I,
    ),
    "sample_link_generation": re.compile(
        r"\b(invent|generate|make up)\b.*\b(sample|portfolio|link)\b",
        re.I,
    ),
}


def main() -> int:
    errors: list[str] = []
    prompt_root = Path("src/bookcraft/prompts")
    for path in sorted(prompt_root.rglob("*")):
        if not path.is_file() or path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for reason, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{path}: forbidden prompt content: {reason}")
    if errors:
        print("prompt verifier failed")
        for error in errors:
            print(error)
        return 1
    print("prompt verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
