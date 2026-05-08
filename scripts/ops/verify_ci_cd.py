from __future__ import annotations

from pathlib import Path

REQUIRED_CI_STEPS = [
    "make lint",
    "make type",
    "make test",
    "make verifier-gates",
    "make security-scan",
    "make dependency-scan",
    "docker compose config",
]

REQUIRED_CD_MARKERS = [
    "build image",
    "run migrations",
    "deploy staging",
    "smoke test",
    "manual production gate",
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
