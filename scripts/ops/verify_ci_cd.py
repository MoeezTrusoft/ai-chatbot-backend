from __future__ import annotations

from pathlib import Path

REQUIRED_CI_STEPS = [
    "lint",
    "type",
    "test",
    "verifier-gates",
    "security-scan",
    "dependency-scan",
    "docker compose config",
    "fail-fast: false",
    "test_postgres_hardening.py",
]
REQUIRED_CD_MARKERS = [
    "build image",
    "smoke test",
    "push: false",
    "docker save",
    "upload-artifact",
]


def main() -> int:
    errors: list[str] = []
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    cd = Path(".github/workflows/cd.yml").read_text(encoding="utf-8")
    for step in REQUIRED_CI_STEPS:
        if step not in ci:
            errors.append(f"ci.yml missing step: {step}")
    for marker in REQUIRED_CD_MARKERS:
        if marker not in cd.casefold():
            errors.append(f"cd.yml missing marker: {marker}")
    if errors:
        print("ci/cd verifier failed")
        for error in errors:
            print(error)
        return 1
    print("ci/cd verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
