from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.infra.config import Settings

EXPECTED_SOURCE_OWNERSHIP: dict[str, dict[str, str]] = {
    "audiobook-production.md": {
        "source_id": "audiobook_production",
        "service_category": "audiobook_production",
        "title_contains": "Audiobook",
    },
    "authors-website.md": {
        "source_id": "authors_website",
        "service_category": "author_website",
        "title_contains": "Website",
    },
    "cover-design-illustration.md": {
        "source_id": "cover_design_illustration",
        "service_category": "cover_design_illustration",
        "title_contains": "Cover",
    },
    "editing-proofreading.md": {
        "source_id": "editing_proofreading",
        "service_category": "editing_proofreading",
        "title_contains": "Editing",
    },
    "formatting.md": {
        "source_id": "formatting",
        "service_category": "interior_formatting",
        "title_contains": "Formatting",
    },
    "ghostwriting.md": {
        "source_id": "ghostwriting",
        "service_category": "ghostwriting",
        "title_contains": "Ghostwriting",
    },
    "marketing-promotion.md": {
        "source_id": "marketing_promotion",
        "service_category": "marketing_promotion",
        "title_contains": "Marketing",
    },
    "publishing-distribution.md": {
        "source_id": "publishing_distribution",
        "service_category": "publishing_distribution",
        "title_contains": "Publishing",
    },
    "video-trailers.md": {
        "source_id": "video_trailers",
        "service_category": "video_trailer",
        "title_contains": "Video",
    },
}

GLOBAL_SOURCE_ALLOWLIST: dict[str, dict[str, str]] = {
    "about-book-craft.md": {
        "source_id": "about_book_craft",
        "service_category": "marketing_promotion",
        "title_contains": "BookCraft",
        "reason": (
            "General company/about content has no dedicated ServiceCategory enum yet. "
            "It is temporarily allowed under marketing_promotion until a global/company "
            "RAG source type is introduced."
        ),
    },
}


@dataclass(frozen=True)
class CoverageIssue:
    path: str
    severity: str
    code: str
    expected: str | None
    actual: str | None
    message: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit RAG source files against expected service ownership."
    )
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--output-dir", default="reports/rag")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when ownership mismatches are found.",
    )
    args = parser.parse_args()

    settings = Settings()
    source_dir = Path(args.source_dir or settings.rag_source_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = build_report(source_dir=source_dir)

    json_path = output_dir / "rag_source_service_category_coverage_report.json"
    md_path = output_dir / "rag_source_service_category_coverage_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    if args.strict and report["summary"]["error_count"] > 0:
        return 1

    return 0


def build_report(*, source_dir: Path) -> dict[str, Any]:
    source_files = sorted(source_dir.glob("*.md")) if source_dir.exists() else []
    issues: list[CoverageIssue] = []
    rows: list[dict[str, Any]] = []

    expected_paths = set(EXPECTED_SOURCE_OWNERSHIP) | set(GLOBAL_SOURCE_ALLOWLIST)
    actual_paths = {path.name for path in source_files}

    for missing_path in sorted(expected_paths - actual_paths):
        issues.append(
            CoverageIssue(
                path=missing_path,
                severity="error",
                code="expected_source_missing",
                expected=missing_path,
                actual=None,
                message=f"Expected RAG source `{missing_path}` is missing.",
            )
        )

    for extra_path in sorted(actual_paths - expected_paths):
        issues.append(
            CoverageIssue(
                path=extra_path,
                severity="error",
                code="unexpected_source_file",
                expected=None,
                actual=extra_path,
                message=f"Unexpected RAG source `{extra_path}` is not in ownership map.",
            )
        )

    for file_path in source_files:
        metadata, body = parse_front_matter(file_path.read_text(encoding="utf-8"))
        file_name = file_path.name

        expected = EXPECTED_SOURCE_OWNERSHIP.get(file_name)
        global_allow = GLOBAL_SOURCE_ALLOWLIST.get(file_name)

        if expected is not None:
            row_issues = validate_expected_source(
                path=file_name,
                metadata=metadata,
                expected=expected,
            )
            ownership_type = "service"
        elif global_allow is not None:
            row_issues = validate_expected_source(
                path=file_name,
                metadata=metadata,
                expected=global_allow,
            )
            ownership_type = "global_allowlist"
            if not row_issues:
                issues.append(
                    CoverageIssue(
                        path=file_name,
                        severity="warning",
                        code="global_source_service_category_proxy",
                        expected=global_allow["service_category"],
                        actual=metadata.get("service_category"),
                        message=global_allow["reason"],
                    )
                )
        else:
            row_issues = []
            ownership_type = "unknown"

        issues.extend(row_issues)

        rows.append(
            {
                "path": file_name,
                "ownership_type": ownership_type,
                "source_id": metadata.get("source_id"),
                "service_category": metadata.get("service_category"),
                "title": metadata.get("title"),
                "section": metadata.get("section"),
                "content_chars": len(body.strip()),
                "issue_count": len(row_issues),
            }
        )

    error_count = sum(1 for item in issues if item.severity == "error")
    warning_count = sum(1 for item in issues if item.severity == "warning")

    return {
        "schema_version": 1,
        "summary": {
            "valid": True,
            "coverage_passed": error_count == 0,
            "generated_at": datetime.now(UTC).isoformat(),
            "source_dir": str(source_dir),
            "source_dir_exists": source_dir.exists(),
            "source_file_count": len(source_files),
            "expected_service_source_count": len(EXPECTED_SOURCE_OWNERSHIP),
            "global_allowlist_count": len(GLOBAL_SOURCE_ALLOWLIST),
            "error_count": error_count,
            "warning_count": warning_count,
            "safety_note": (
                "Observation-only ownership audit. It does not create Elasticsearch "
                "indices, embed content, bulk index documents, change aliases, or "
                "enable production RAG."
            ),
        },
        "sources": rows,
        "issues": [issue_to_dict(item) for item in issues],
        "expected_source_ownership": EXPECTED_SOURCE_OWNERSHIP,
        "global_source_allowlist": GLOBAL_SOURCE_ALLOWLIST,
    }


def validate_expected_source(
    *,
    path: str,
    metadata: dict[str, str],
    expected: dict[str, str],
) -> list[CoverageIssue]:
    issues: list[CoverageIssue] = []

    for field in ("source_id", "service_category"):
        expected_value = expected[field]
        actual_value = metadata.get(field)
        if actual_value != expected_value:
            issues.append(
                CoverageIssue(
                    path=path,
                    severity="error",
                    code=f"{field}_mismatch",
                    expected=expected_value,
                    actual=actual_value,
                    message=(f"`{field}` should be `{expected_value}` but found `{actual_value}`."),
                )
            )

    title = metadata.get("title") or ""
    title_contains = expected.get("title_contains")
    if title_contains and title_contains.lower() not in title.lower():
        issues.append(
            CoverageIssue(
                path=path,
                severity="warning",
                code="title_alignment_suspicious",
                expected=title_contains,
                actual=title,
                message=(
                    f"Title should contain `{title_contains}` for easier source ownership review."
                ),
            )
        )

    return issues


def parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw

    metadata: dict[str, str] = {}
    body_start = 0

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = index + 1
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")

    if body_start == 0:
        return metadata, ""

    return metadata, "\n".join(lines[body_start:])


def issue_to_dict(issue: CoverageIssue) -> dict[str, Any]:
    return {
        "path": issue.path,
        "severity": issue.severity,
        "code": issue.code,
        "expected": issue.expected,
        "actual": issue.actual,
        "message": issue.message,
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]

    lines = [
        "# RAG Source Service Category Coverage Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Coverage passed: `{summary['coverage_passed']}`",
        f"- Source dir: `{summary['source_dir']}`",
        f"- Source files: `{summary['source_file_count']}`",
        f"- Expected service sources: `{summary['expected_service_source_count']}`",
        f"- Global allowlist sources: `{summary['global_allowlist_count']}`",
        f"- Errors: `{summary['error_count']}`",
        f"- Warnings: `{summary['warning_count']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Sources",
        "",
        "| File | Ownership | Source ID | Service | Title | Issues |",
        "|---|---|---|---|---|---:|",
    ]

    for source in report["sources"]:
        lines.append(
            "| `{path}` | `{ownership}` | `{source_id}` | `{service}` | "
            "`{title}` | {issues} |".format(
                path=source["path"],
                ownership=source["ownership_type"],
                source_id=source["source_id"] or "",
                service=source["service_category"] or "",
                title=source["title"] or "",
                issues=source["issue_count"],
            )
        )

    lines.extend(
        [
            "",
            "## Issues",
            "",
            "| Severity | Code | File | Expected | Actual | Message |",
            "|---|---|---|---|---|---|",
        ]
    )

    if report["issues"]:
        for issue in report["issues"]:
            lines.append(
                "| `{severity}` | `{code}` | `{path}` | `{expected}` | `{actual}` | "
                "{message} |".format(
                    severity=issue["severity"],
                    code=issue["code"],
                    path=issue["path"],
                    expected=issue["expected"] or "",
                    actual=issue["actual"] or "",
                    message=issue["message"],
                )
            )
    else:
        lines.append("| _none_ |  |  |  |  |  |")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
