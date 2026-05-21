from __future__ import annotations

import re
import tomllib
from pathlib import Path

FORBIDDEN_DEPENDENCY_PATTERNS = [
    re.compile(r"\s@\s"),
    re.compile(r"git\+"),
    re.compile(r"file:"),
    re.compile(r"path\s*="),
]


def main() -> int:
    errors: list[str] = []
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    dependencies = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for values in optional.values():
            if isinstance(values, list):
                dependencies.extend(str(value) for value in values)
    for dependency in dependencies:
        for pattern in FORBIDDEN_DEPENDENCY_PATTERNS:
            if pattern.search(str(dependency)):
                errors.append(f"forbidden direct dependency reference: {dependency}")
    if not Path("uv.lock").exists():
        errors.append("uv.lock is missing")
    if 'requires-python = ">=3.12"' not in Path("pyproject.toml").read_text(encoding="utf-8"):
        errors.append("pyproject.toml must require Python >=3.12")
    if errors:
        print("dependency scan failed")
        for error in errors:
            print(error)
        return 1
    print("dependency scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
