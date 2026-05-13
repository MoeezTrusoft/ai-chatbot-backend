from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.domain.enums import ServiceCategory
from bookcraft.infra.config import Settings

SERVICE_KEYWORDS: dict[ServiceCategory, tuple[str, ...]] = {
    ServiceCategory.GHOSTWRITING: ("ghostwriting", "ghostwriter", "writing", "manuscript"),
    ServiceCategory.EDITING_PROOFREADING: (
        "editing",
        "proofreading",
        "proofread",
        "editor",
        "copyedit",
    ),
    ServiceCategory.COVER_DESIGN_ILLUSTRATION: (
        "cover",
        "illustration",
        "illustrator",
        "book design",
    ),
    ServiceCategory.INTERIOR_FORMATTING: (
        "formatting",
        "interior",
        "layout",
        "typesetting",
        "ebook",
        "print",
    ),
    ServiceCategory.AUDIOBOOK_PRODUCTION: ("audiobook", "audio book", "narration", "voice"),
    ServiceCategory.PUBLISHING_DISTRIBUTION: (
        "publishing",
        "distribution",
        "kdp",
        "ingramspark",
        "isbn",
    ),
    ServiceCategory.MARKETING_PROMOTION: (
        "marketing",
        "promotion",
        "launch",
        "campaign",
        "ads",
    ),
    ServiceCategory.AUTHOR_WEBSITE: ("website", "author site", "landing page", "web"),
    ServiceCategory.VIDEO_TRAILER: ("video", "trailer", "book trailer", "reel"),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an observation-only RAG source front matter repair plan."
    )
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--output-dir", default="reports/rag")
    args = parser.parse_args()

    settings = Settings()
    source_dir = Path(args.source_dir or settings.rag_source_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = build_report(source_dir=source_dir)

    json_path = output_dir / "rag_source_frontmatter_repair_plan.json"
    md_path = output_dir / "rag_source_frontmatter_repair_plan.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0


def build_report(*, source_dir: Path) -> dict[str, Any]:
    source_files = sorted(source_dir.glob("**/*.md")) if source_dir.exists() else []
    repairs = [build_repair_item(path, source_dir=source_dir) for path in source_files]

    summary = {
        "valid": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_dir": str(source_dir),
        "source_dir_exists": source_dir.exists(),
        "source_file_count": len(source_files),
        "repair_item_count": len(repairs),
        "high_confidence_count": sum(1 for item in repairs if item["confidence"] == "high"),
        "medium_confidence_count": sum(1 for item in repairs if item["confidence"] == "medium"),
        "low_confidence_count": sum(1 for item in repairs if item["confidence"] == "low"),
        "safety_note": (
            "Observation-only repair plan. It does not modify source markdown, "
            "does not create Elasticsearch indices, does not embed content, "
            "and does not change aliases."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "repair_items": repairs,
        "instructions": [
            "Review every proposed front matter block manually.",
            "Apply source markdown changes in a separate branch.",
            "Run verify_rag_source_metadata.py --strict after repairs.",
            "Do not run indexing until metadata verifier is clean.",
        ],
    }


def build_repair_item(path: Path, *, source_dir: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    relative_path = str(path.relative_to(source_dir))
    existing_metadata, body = parse_front_matter(raw)

    title = existing_metadata.get("title") or infer_title(path=path, body=body or raw)
    source_id = existing_metadata.get("source_id") or slugify(path.stem)
    service_category, confidence, matched_keywords = infer_service_category(
        path=path,
        text=f"{relative_path}\n{body or raw}",
    )
    section = existing_metadata.get("section") or infer_section(path=path, body=body or raw)
    content_version = existing_metadata.get("content_version") or "v1"

    proposed = {
        "title": title,
        "source_id": source_id,
        "service_category": service_category.value,
        "section": section,
        "content_version": content_version,
        "allowed_for_response": "true",
        "tags": build_tags(service_category=service_category, section=section),
    }

    return {
        "path": relative_path,
        "confidence": confidence,
        "matched_keywords": matched_keywords,
        "existing_metadata": existing_metadata,
        "proposed_front_matter": proposed,
        "proposed_block": front_matter_block(proposed),
        "notes": notes_for_item(
            confidence=confidence,
            existing_metadata=existing_metadata,
            body=body or raw,
        ),
    }


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


def infer_title(*, path: Path, body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem.replace("_", " ").replace("-", " ").title()


def infer_section(*, path: Path, body: str) -> str:
    text = f"{path.stem} {body[:500]}".lower()
    if "faq" in text or "question" in text:
        return "faq"
    if "process" in text or "step" in text:
        return "process"
    if "pricing" in text or "cost" in text:
        return "pricing_context"
    if "timeline" in text or "time" in text:
        return "timeline_context"
    if "portfolio" in text or "sample" in text:
        return "portfolio_context"
    return "overview"


def infer_service_category(*, path: Path, text: str) -> tuple[ServiceCategory, str, list[str]]:
    haystack = text.lower()
    scores: dict[ServiceCategory, list[str]] = {}

    for service, keywords in SERVICE_KEYWORDS.items():
        matches = [keyword for keyword in keywords if keyword in haystack]
        if matches:
            scores[service] = matches

    if not scores:
        return ServiceCategory.GHOSTWRITING, "low", []

    ranked = sorted(scores.items(), key=lambda item: (-len(item[1]), item[0].value))
    service, matches = ranked[0]

    if len(matches) >= 2:
        confidence = "high"
    else:
        confidence = "medium"

    return service, confidence, matches


def build_tags(*, service_category: ServiceCategory, section: str) -> str:
    return f"[{service_category.value}, {section}, rag]"


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "rag_source"


def front_matter_block(metadata: dict[str, str]) -> str:
    return "\n".join(
        [
            "---",
            f"title: {metadata['title']}",
            f"source_id: {metadata['source_id']}",
            f"service_category: {metadata['service_category']}",
            f"section: {metadata['section']}",
            f"content_version: {metadata['content_version']}",
            f"allowed_for_response: {metadata['allowed_for_response']}",
            f"tags: {metadata['tags']}",
            "---",
        ]
    )


def notes_for_item(
    *,
    confidence: str,
    existing_metadata: dict[str, str],
    body: str,
) -> list[str]:
    notes: list[str] = []
    if not existing_metadata:
        notes.append("source currently has no front matter")
    if confidence == "low":
        notes.append("service_category inference is low confidence; manual review required")
    if not body.strip():
        notes.append("body appears empty; repair metadata only after content is added")
    return notes


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RAG Source Front Matter Repair Plan",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Source dir: `{summary['source_dir']}`",
        f"- Source dir exists: `{summary['source_dir_exists']}`",
        f"- Source file count: `{summary['source_file_count']}`",
        f"- Repair item count: `{summary['repair_item_count']}`",
        f"- High confidence: `{summary['high_confidence_count']}`",
        f"- Medium confidence: `{summary['medium_confidence_count']}`",
        f"- Low confidence: `{summary['low_confidence_count']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Repair Items",
        "",
    ]

    for item in report["repair_items"]:
        lines.extend(
            [
                f"### `{item['path']}`",
                "",
                f"- Confidence: `{item['confidence']}`",
                f"- Matched keywords: `{', '.join(item['matched_keywords']) or 'none'}`",
                f"- Notes: `{', '.join(item['notes']) or 'none'}`",
                "",
                "```yaml",
                item["proposed_block"],
                "```",
                "",
            ]
        )

    if not report["repair_items"]:
        lines.append("_No source files found._")
        lines.append("")

    lines.extend(
        [
            "## Instructions",
            "",
        ]
    )

    for instruction in report["instructions"]:
        lines.append(f"- {instruction}")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
