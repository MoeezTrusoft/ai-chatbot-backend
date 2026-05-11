from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ALLOWED_OUTPUT_SUFFIXES = {"html", "pdf"}
_DOCUMENT_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$", re.IGNORECASE)

_PLACEHOLDER_PATTERNS = (
    "REPLACE_WITH_APPROVED_VALUE",
    "REPLACE_WITH",
    "TBD",
    "TO BE DECIDED",
    "TODO",
    "LOREM IPSUM",
    "[[",
    "]]",
    "{{",
    "}}",
)

_ACTIVE_CONTENT_PATTERNS = (
    "<script",
    "javascript:",
    "onerror=",
    "onload=",
    "onclick=",
)


def safe_output_root(output_dir: str | Path) -> Path:
    root = Path(output_dir).expanduser().resolve()
    if root == root.parent:
        raise ValueError("document output directory cannot be filesystem root")
    if root.exists() and not root.is_dir():
        raise ValueError("document output path must be a directory")
    return root


def safe_document_output_path(
    *,
    output_root: Path,
    kind: Any,
    document_id: str,
    suffix: str,
) -> Path:
    normalized_suffix = suffix.lower().lstrip(".")
    if normalized_suffix not in _ALLOWED_OUTPUT_SUFFIXES:
        raise ValueError("unsupported document output suffix")
    if not _DOCUMENT_ID_PATTERN.fullmatch(document_id):
        raise ValueError("unsafe document id")

    kind_value = _document_kind_value(kind)
    root = output_root.expanduser().resolve()
    target = (root / kind_value / f"{document_id}.{normalized_suffix}").resolve()

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("document output path escapes output root") from exc

    return target


def rendered_document_safety_errors(rendered_html: str) -> list[str]:
    errors: list[str] = []
    normalized = rendered_html.casefold()

    for pattern in _PLACEHOLDER_PATTERNS:
        if pattern.casefold() in normalized:
            errors.append(f"unresolved placeholder-like text remains: {pattern}")

    for pattern in _ACTIVE_CONTENT_PATTERNS:
        if pattern in normalized:
            errors.append(f"active content is not allowed in generated documents: {pattern}")

    if re.search(r"<iframe\b|<object\b|<embed\b", normalized):
        errors.append("embedded active content is not allowed in generated documents")

    return errors


def reject_placeholder_like_value(value: str, *, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")

    normalized = stripped.casefold()
    blocked_fragments = (
        "placeholder",
        "replace_with",
        "replace with",
        "tbd",
        "to be decided",
        "todo",
        "guess",
        "dummy",
        "lorem ipsum",
    )
    if any(fragment in normalized for fragment in blocked_fragments):
        raise ValueError(f"{field_name} must come from approved deterministic/customer data")

    return stripped


def _document_kind_value(kind: Any) -> str:
    value = getattr(kind, "value", kind)
    return str(value).strip().lower()
