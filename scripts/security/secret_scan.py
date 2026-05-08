from __future__ import annotations

import re
from pathlib import Path

EXCLUDED_DIRS = {
    ".git",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".uv-cache",
    ".venv",
    "data/generated",
    "htmlcov",
}
EXCLUDED_SUFFIXES = {
    ".docx",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".zip",
    ".pyc",
    ".sqlite",
}
EXCLUDED_FILENAMES = {
    ".env",
}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "assigned_secret": re.compile(
        r"(?i)\b(secret|token|api[_-]?key|password)\b\s*[:=]\s*['\"]?"
        r"([A-Za-z0-9_\-./+=]{8,})"
    ),
}
PLACEHOLDER_VALUES = {
    "changeme",
    "example",
    "placeholder",
    "bookcraft_dev",
    "bookcraft",
    "admin",
}


def main() -> int:
    findings: list[str] = []
    for path in sorted(Path(".").rglob("*")):
        if not path.is_file() or _excluded(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                if name == "assigned_secret" and _allowed_assignment(match):
                    continue
                findings.append(f"{path}:{_line_number(text, match.start())}: {name}")
    if findings:
        print("secret scan failed")
        for finding in findings:
            print(finding)
        return 1
    print("secret scan passed")
    return 0


def _excluded(path: Path) -> bool:
    if path.name in EXCLUDED_FILENAMES:
        return True
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    parts = set(path.parts)
    return any(
        excluded in parts or str(path).startswith(excluded + "/") for excluded in EXCLUDED_DIRS
    )


def _allowed_assignment(match: re.Match[str]) -> bool:
    value = match.group(2).strip().strip("'\"")
    return (
        value == ""
        or value.casefold() in PLACEHOLDER_VALUES
        or value.startswith(("settings.", "self.", "config."))
        or value.startswith("${")
        or value[0].isupper()
    )


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


if __name__ == "__main__":
    raise SystemExit(main())
