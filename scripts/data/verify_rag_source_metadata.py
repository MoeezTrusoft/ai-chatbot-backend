from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.domain.enums import SalesStage, ServiceCategory
from bookcraft.infra.config import Settings

REQUIRED_FIELDS = [
    "title",
    "source_id",
    "service_category",
    "section",
    "content_version",
]

OPTIONAL_FIELDS = [
    "source_type",
    "subservice",
    "audience",
    "funnel_stage",
    "source_filename",
    "tags",
    "allowed_for_response",
]


@dataclass(frozen=True)
class SourceIssue:
    path: str
    severity: str
    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class SourceSummary:
    path: str
    source_id: str | None
    title: str | None
    service_category: str | None
    section: str | None
    content_version: str | None
    content_chars: int
    issue_count: int


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify RAG source markdown metadata without indexing."
    )
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--output-dir", default="reports/rag")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when metadata is not ready for indexing.",
    )
    args = parser.parse_args()

    settings = Settings()
    source_dir = Path(args.source_dir or settings.rag_source_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = build_report(source_dir=source_dir)

    json_path = output_dir / "rag_source_metadata_report.json"
    md_path = output_dir / "rag_source_metadata_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    if args.strict and not report["summary"]["ready_for_indexing"]:
        return 1
    return 0


def build_report(*, source_dir: Path) -> dict[str, Any]:
    source_files = sorted(source_dir.glob("**/*.md")) if source_dir.exists() else []
    issues: list[SourceIssue] = []
    summaries: list[SourceSummary] = []

    source_id_to_paths: dict[str, list[str]] = {}

    if not source_dir.exists():
        issues.append(
            SourceIssue(
                path=str(source_dir),
                severity="error",
                code="source_dir_missing",
                message="RAG source directory does not exist.",
            )
        )

    if source_dir.exists() and not source_files:
        issues.append(
            SourceIssue(
                path=str(source_dir),
                severity="warning",
                code="no_markdown_sources_found",
                message="RAG source directory contains no markdown files.",
            )
        )

    for file_path in source_files:
        relative_path = str(file_path.relative_to(source_dir))
        raw = file_path.read_text(encoding="utf-8")
        metadata, body = parse_front_matter(raw)

        file_issues = validate_file(
            path=relative_path,
            metadata=metadata,
            body=body,
        )
        issues.extend(file_issues)

        source_id = clean(metadata.get("source_id"))
        if source_id:
            source_id_to_paths.setdefault(source_id, []).append(relative_path)

        summaries.append(
            SourceSummary(
                path=relative_path,
                source_id=source_id,
                title=clean(metadata.get("title")),
                service_category=clean(metadata.get("service_category")),
                section=clean(metadata.get("section")),
                content_version=clean(metadata.get("content_version")),
                content_chars=len(body.strip()),
                issue_count=len(file_issues),
            )
        )

    for source_id, paths in sorted(source_id_to_paths.items()):
        if len(paths) <= 1:
            continue
        for path in paths:
            issues.append(
                SourceIssue(
                    path=path,
                    severity="error",
                    code="duplicate_source_id",
                    field="source_id",
                    message=f"Duplicate source_id `{source_id}` appears in {paths}.",
                )
            )

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    ready_for_indexing = error_count == 0 and bool(source_files)

    summary = {
        "valid": True,
        "ready_for_indexing": ready_for_indexing,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_dir": str(source_dir),
        "source_dir_exists": source_dir.exists(),
        "source_file_count": len(source_files),
        "error_count": error_count,
        "warning_count": warning_count,
        "required_fields": REQUIRED_FIELDS,
        "allowed_service_categories": [item.value for item in ServiceCategory],
        "allowed_funnel_stages": [item.value for item in SalesStage],
        "safety_note": (
            "Observation-only verifier. It does not create Elasticsearch indices, "
            "does not embed content, and does not change aliases."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "sources": [source_summary(item) for item in summaries],
        "issues": [source_issue(issue) for issue in issues],
    }


def validate_file(
    *,
    path: str,
    metadata: dict[str, str],
    body: str,
) -> list[SourceIssue]:
    issues: list[SourceIssue] = []

    if not metadata:
        issues.append(
            SourceIssue(
                path=path,
                severity="error",
                code="front_matter_missing",
                message="Markdown file is missing YAML-style front matter.",
            )
        )

    for field in REQUIRED_FIELDS:
        if not clean(metadata.get(field)):
            issues.append(
                SourceIssue(
                    path=path,
                    severity="error",
                    code="required_field_missing",
                    field=field,
                    message=f"Required metadata field `{field}` is missing.",
                )
            )

    service_category = clean(metadata.get("service_category"))
    if service_category and service_category not in {item.value for item in ServiceCategory}:
        issues.append(
            SourceIssue(
                path=path,
                severity="error",
                code="invalid_service_category",
                field="service_category",
                message=f"Invalid service_category `{service_category}`.",
            )
        )

    funnel_stage = clean(metadata.get("funnel_stage"))
    if funnel_stage and funnel_stage not in {item.value for item in SalesStage}:
        issues.append(
            SourceIssue(
                path=path,
                severity="error",
                code="invalid_funnel_stage",
                field="funnel_stage",
                message=f"Invalid funnel_stage `{funnel_stage}`.",
            )
        )

    allowed_for_response = clean(metadata.get("allowed_for_response"))
    if allowed_for_response and allowed_for_response.lower() not in {"true", "false"}:
        issues.append(
            SourceIssue(
                path=path,
                severity="error",
                code="invalid_allowed_for_response",
                field="allowed_for_response",
                message="allowed_for_response must be true or false.",
            )
        )

    tags = clean(metadata.get("tags"))
    if tags and not (tags.startswith("[") and tags.endswith("]")):
        issues.append(
            SourceIssue(
                path=path,
                severity="warning",
                code="tags_not_list_style",
                field="tags",
                message="tags should use list style, for example [ghostwriting, faq].",
            )
        )

    unknown_fields = sorted(set(metadata) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    for field in unknown_fields:
        issues.append(
            SourceIssue(
                path=path,
                severity="warning",
                code="unknown_metadata_field",
                field=field,
                message=f"Unknown metadata field `{field}`.",
            )
        )

    if not body.strip():
        issues.append(
            SourceIssue(
                path=path,
                severity="error",
                code="empty_content",
                message="Markdown body content is empty.",
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


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def source_summary(item: SourceSummary) -> dict[str, Any]:
    return {
        "path": item.path,
        "source_id": item.source_id,
        "title": item.title,
        "service_category": item.service_category,
        "section": item.section,
        "content_version": item.content_version,
        "content_chars": item.content_chars,
        "issue_count": item.issue_count,
    }


def source_issue(issue: SourceIssue) -> dict[str, Any]:
    return {
        "path": issue.path,
        "severity": issue.severity,
        "code": issue.code,
        "field": issue.field,
        "message": issue.message,
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RAG Source Metadata Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Ready for indexing: `{summary['ready_for_indexing']}`",
        f"- Source dir: `{summary['source_dir']}`",
        f"- Source dir exists: `{summary['source_dir_exists']}`",
        f"- Source file count: `{summary['source_file_count']}`",
        f"- Error count: `{summary['error_count']}`",
        f"- Warning count: `{summary['warning_count']}`",
        "",
        "## Required Fields",
        "",
    ]

    for field in summary["required_fields"]:
        lines.append(f"- `{field}`")

    lines.extend(
        [
            "",
            "## Sources",
            "",
            "| File | Source ID | Service | Section | Content Version | Issues |",
            "|---|---|---|---|---|---:|",
        ]
    )

    for source in report["sources"]:
        lines.append(
            "| `{path}` | `{source_id}` | `{service}` | `{section}` | "
            "`{version}` | {issues} |".format(
                path=source["path"],
                source_id=source["source_id"] or "",
                service=source["service_category"] or "",
                section=source["section"] or "",
                version=source["content_version"] or "",
                issues=source["issue_count"],
            )
        )

    if not report["sources"]:
        lines.append("| _none_ |  |  |  |  | 0 |")

    lines.extend(
        [
            "",
            "## Issues",
            "",
            "| Severity | Code | File | Field | Message |",
            "|---|---|---|---|---|",
        ]
    )

    for issue in report["issues"]:
        lines.append(
            "| `{severity}` | `{code}` | `{path}` | `{field}` | {message} |".format(
                severity=issue["severity"],
                code=issue["code"],
                path=issue["path"],
                field=issue["field"] or "",
                message=issue["message"],
            )
        )

    if not report["issues"]:
        lines.append("| _none_ |  |  |  |  |")

    lines.extend(
        [
            "",
            "## Safety Note",
            "",
            summary["safety_note"],
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
