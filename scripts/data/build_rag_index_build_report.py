from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from elasticsearch import Elasticsearch

from bookcraft.infra.config import Settings


@dataclass(frozen=True)
class SourceFileSummary:
    path: str
    size_bytes: int
    checksum: str
    heading_count: int
    estimated_chunk_count: int
    has_required_front_matter: bool
    missing_metadata_fields: list[str]


REQUIRED_METADATA_FIELDS = [
    "title",
    "source_id",
    "service_category",
    "section",
    "content_version",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an observational RAG index readiness report."
    )
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--output-dir", default="reports/rag")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--check-externals", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    source_dir = Path(args.source_dir or settings.rag_source_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = build_report(
        settings=settings,
        source_dir=source_dir,
        chunk_size=args.chunk_size,
        check_externals=args.check_externals,
    )

    json_path = output_dir / "rag_index_build_report.json"
    md_path = output_dir / "rag_index_build_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


def build_report(
    *,
    settings: Settings,
    source_dir: Path,
    chunk_size: int,
    check_externals: bool,
) -> dict[str, Any]:
    source_files = sorted(source_dir.glob("**/*.md")) if source_dir.exists() else []
    file_summaries = [
        summarize_source_file(path, source_dir=source_dir, chunk_size=chunk_size)
        for path in source_files
    ]

    missing_metadata_count = sum(1 for item in file_summaries if item.missing_metadata_fields)
    estimated_chunk_count = sum(item.estimated_chunk_count for item in file_summaries)

    elasticsearch_status = check_elasticsearch(settings) if check_externals else skipped()
    tei_status = check_tei(settings) if check_externals else skipped()

    warnings: list[str] = []
    if not source_dir.exists():
        warnings.append("source_dir_missing")
    if not source_files:
        warnings.append("no_markdown_sources_found")
    if missing_metadata_count:
        warnings.append("metadata_gaps_found")

    summary = {
        "valid": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_dir": str(source_dir),
        "source_dir_exists": source_dir.exists(),
        "source_file_count": len(source_files),
        "estimated_chunk_count": estimated_chunk_count,
        "files_missing_metadata_count": missing_metadata_count,
        "chunk_size": chunk_size,
        "rag_index_alias": settings.rag_index_alias,
        "rag_index_version": settings.rag_index_version,
        "embedding_dimensions": settings.embedding_dimensions,
        "elasticsearch_status": elasticsearch_status,
        "tei_status": tei_status,
        "warnings": warnings,
        "safety_note": (
            "Observation-only report. No Elasticsearch index is created, "
            "no documents are ingested, and no alias is changed."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "source_files": [
            {
                "path": item.path,
                "size_bytes": item.size_bytes,
                "checksum": item.checksum,
                "heading_count": item.heading_count,
                "estimated_chunk_count": item.estimated_chunk_count,
                "has_required_front_matter": item.has_required_front_matter,
                "missing_metadata_fields": item.missing_metadata_fields,
            }
            for item in file_summaries
        ],
        "required_metadata_fields": REQUIRED_METADATA_FIELDS,
        "recommended_next_steps": [
            "add curated markdown source files under data/rag-corpus/source_markdown",
            "add metadata/front matter verifier",
            "add deterministic chunk builder",
            "add TEI embedding batch tool",
            "add Elasticsearch mapping/index creation tool",
            "add bulk indexing with report artifacts",
            "add smoke report before alias swap",
        ],
    }


def summarize_source_file(
    path: Path,
    *,
    source_dir: Path,
    chunk_size: int,
) -> SourceFileSummary:
    raw = path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    heading_count = sum(1 for line in raw.splitlines() if line.lstrip().startswith("#"))
    estimated_chunk_count = max(1, (len(raw) + chunk_size - 1) // chunk_size) if raw else 0

    front_matter = extract_front_matter(raw)
    missing = [
        field for field in REQUIRED_METADATA_FIELDS if not str(front_matter.get(field, "")).strip()
    ]

    return SourceFileSummary(
        path=str(path.relative_to(source_dir)),
        size_bytes=path.stat().st_size,
        checksum=checksum,
        heading_count=heading_count,
        estimated_chunk_count=estimated_chunk_count,
        has_required_front_matter=not missing,
        missing_metadata_fields=missing,
    )


def extract_front_matter(raw: str) -> dict[str, str]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata


def check_elasticsearch(settings: Settings) -> dict[str, Any]:
    try:
        client = Elasticsearch(settings.elasticsearch_url, request_timeout=2)
        try:
            info = client.info()
            alias_exists = client.indices.exists_alias(name=settings.rag_index_alias)
            return {
                "checked": True,
                "reachable": True,
                "cluster_name": info.get("cluster_name"),
                "version": info.get("version", {}).get("number"),
                "rag_alias_exists": bool(alias_exists),
                "error": None,
            }
        finally:
            client.close()
    except Exception as exc:
        return {
            "checked": True,
            "reachable": False,
            "cluster_name": None,
            "version": None,
            "rag_alias_exists": False,
            "error": exc.__class__.__name__ + ": " + str(exc),
        }


def check_tei(settings: Settings) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=2) as client:
            response = client.post(
                f"{settings.tei_url.rstrip('/')}/embed",
                json={"inputs": "rag readiness smoke"},
            )
            response.raise_for_status()
            data = response.json()
        vector = data[0] if isinstance(data, list) and data else []
        return {
            "checked": True,
            "reachable": True,
            "embedding_dimensions": len(vector) if isinstance(vector, list) else None,
            "dimension_matches": (
                len(vector) == settings.embedding_dimensions if isinstance(vector, list) else False
            ),
            "error": None,
        }
    except Exception as exc:
        return {
            "checked": True,
            "reachable": False,
            "embedding_dimensions": None,
            "dimension_matches": False,
            "error": exc.__class__.__name__ + ": " + str(exc),
        }


def skipped() -> dict[str, Any]:
    return {
        "checked": False,
        "reachable": None,
        "error": "skipped; pass --check-externals to test connectivity",
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RAG Index Build Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Source dir: `{summary['source_dir']}`",
        f"- Source dir exists: `{summary['source_dir_exists']}`",
        f"- Source file count: `{summary['source_file_count']}`",
        f"- Estimated chunk count: `{summary['estimated_chunk_count']}`",
        f"- Files missing metadata: `{summary['files_missing_metadata_count']}`",
        f"- RAG index alias: `{summary['rag_index_alias']}`",
        f"- RAG index version: `{summary['rag_index_version']}`",
        f"- Embedding dimensions: `{summary['embedding_dimensions']}`",
        f"- Warnings: `{', '.join(summary['warnings']) or 'none'}`",
        "",
        "## Elasticsearch Status",
        "",
        "```json",
        json.dumps(summary["elasticsearch_status"], indent=2, sort_keys=True),
        "```",
        "",
        "## TEI Status",
        "",
        "```json",
        json.dumps(summary["tei_status"], indent=2, sort_keys=True),
        "```",
        "",
        "## Source Files",
        "",
        "| File | Size | Headings | Estimated Chunks | Missing Metadata |",
        "|---|---:|---:|---:|---|",
    ]

    for item in report["source_files"]:
        lines.append(
            "| `{path}` | {size} | {headings} | {chunks} | {missing} |".format(
                path=item["path"],
                size=item["size_bytes"],
                headings=item["heading_count"],
                chunks=item["estimated_chunk_count"],
                missing=", ".join(item["missing_metadata_fields"]) or "none",
            )
        )

    if not report["source_files"]:
        lines.append("| _none_ | 0 | 0 | 0 | n/a |")

    lines.extend(
        [
            "",
            "## Safety Note",
            "",
            summary["safety_note"],
            "",
            "## Recommended Next Steps",
            "",
        ]
    )

    for step in report["recommended_next_steps"]:
        lines.append(f"- {step}")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
