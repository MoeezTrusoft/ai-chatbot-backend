from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from elasticsearch import Elasticsearch, NotFoundError, helpers

from bookcraft.domain.enums import SalesStage, ServiceCategory
from bookcraft.infra.config import Settings


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    relative_path: str
    metadata: dict[str, str]
    body: str
    checksum: str


@dataclass(frozen=True)
class ChunkDocument:
    chunk_id: str
    content: str
    checksum: str
    source: SourceDocument
    chunk_index: int


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a versioned Elasticsearch RAG index from tracked markdown."
    )
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--output-dir", default="reports/rag")
    parser.add_argument("--index-name", default=None)
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--swap-alias", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    source_dir = Path(args.source_dir or settings.rag_source_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    index_name = args.index_name or versioned_index_name(settings)
    report = build_index_report(
        settings=settings,
        source_dir=source_dir,
        index_name=index_name,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        apply=args.apply,
        swap_alias=args.swap_alias,
    )

    json_path = output_dir / "rag_elasticsearch_index_report.json"
    md_path = output_dir / "rag_elasticsearch_index_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


def build_index_report(
    *,
    settings: Settings,
    source_dir: Path,
    index_name: str,
    chunk_size: int,
    chunk_overlap: int,
    apply: bool,
    swap_alias: bool,
) -> dict[str, Any]:
    sources = load_sources(source_dir)
    chunks = build_chunks(
        sources=sources,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    mapping = elasticsearch_mapping(settings.embedding_dimensions)

    actions: list[dict[str, Any]] = []
    indexed_count = 0
    alias_swapped = False
    errors: list[str] = []

    if apply:
        try:
            vectors = embed_chunks(
                chunks=chunks,
                settings=settings,
            )
            docs = [
                chunk_to_document(
                    chunk=chunk,
                    vector=vector,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
            actions = [
                {
                    "_op_type": "index",
                    "_index": index_name,
                    "_id": doc["chunk_id"],
                    "_source": doc,
                }
                for doc in docs
            ]
            client = elasticsearch_client(settings)
            try:
                if client.indices.exists(index=index_name):
                    raise RuntimeError(f"Index already exists: {index_name}")

                client.indices.create(index=index_name, **mapping)
                indexed_count, bulk_errors = helpers.bulk(
                    client,
                    actions,
                    stats_only=False,
                    raise_on_error=False,
                )
                if bulk_errors:
                    errors.append(f"bulk_errors={bulk_errors!r}")

                client.indices.refresh(index=index_name)

                if swap_alias and not bulk_errors:
                    swap_index_alias(
                        client=client,
                        alias=settings.rag_index_alias,
                        new_index=index_name,
                    )
                    alias_swapped = True
            finally:
                client.close()
        except Exception as exc:  # noqa: BLE001 - report tooling must surface all failures.
            errors.append(exc.__class__.__name__ + ": " + str(exc))

    valid = bool(sources) and bool(chunks) and not errors

    summary = {
        "valid": valid,
        "generated_at": datetime.now(UTC).isoformat(),
        "apply": apply,
        "swap_alias_requested": swap_alias,
        "alias_swapped": alias_swapped,
        "source_dir": str(source_dir),
        "source_file_count": len(sources),
        "chunk_count": len(chunks),
        "indexed_count": indexed_count,
        "index_name": index_name,
        "rag_index_alias": settings.rag_index_alias,
        "embedding_dimensions": settings.embedding_dimensions,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "errors": errors,
        "safety_note": (
            "Dry-run by default. Index creation requires --apply. Alias movement "
            "requires --apply --swap-alias. This tool does not enable production RAG."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "mapping": mapping,
        "sources": [
            {
                "path": source.relative_path,
                "checksum": source.checksum,
                "title": source.metadata.get("title"),
                "source_id": source.metadata.get("source_id"),
                "service_category": source.metadata.get("service_category"),
                "section": source.metadata.get("section"),
                "content_version": source.metadata.get("content_version"),
            }
            for source in sources
        ],
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "source_path": chunk.source.relative_path,
                "source_id": chunk.source.metadata.get("source_id"),
                "chunk_index": chunk.chunk_index,
                "content_chars": len(chunk.content),
                "checksum": chunk.checksum,
            }
            for chunk in chunks
        ],
    }


def load_sources(source_dir: Path) -> list[SourceDocument]:
    if not source_dir.exists():
        raise FileNotFoundError(f"RAG source dir does not exist: {source_dir}")

    sources: list[SourceDocument] = []
    for path in sorted(source_dir.glob("**/*.md")):
        raw = path.read_text(encoding="utf-8")
        metadata, body = parse_front_matter(raw)
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        sources.append(
            SourceDocument(
                path=path,
                relative_path=str(path.relative_to(source_dir)),
                metadata=metadata,
                body=body,
                checksum=checksum,
            )
        )
    return sources


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

    return metadata, "\n".join(lines[body_start:]).strip()


def build_chunks(
    *,
    sources: list[SourceDocument],
    chunk_size: int,
    chunk_overlap: int,
) -> list[ChunkDocument]:
    chunks: list[ChunkDocument] = []

    for source in sources:
        source_id = required_metadata(source, "source_id")
        pieces = chunk_text(
            source.body,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        for index, content in enumerate(pieces, start=1):
            chunk_id = f"{source_id}::{index:04d}"
            checksum = hashlib.sha256(f"{source.checksum}:{index}:{content}".encode()).hexdigest()
            chunks.append(
                ChunkDocument(
                    chunk_id=chunk_id,
                    content=content,
                    checksum=checksum,
                    source=source,
                    chunk_index=index,
                )
            )

    return chunks


def chunk_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not text:
        return []

    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = overlap_tail(current, chunk_overlap)

        if len(paragraph) <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip() if current else paragraph
            continue

        for part in split_long_text(paragraph, chunk_size=chunk_size):
            if current:
                chunks.append(current)
                current = overlap_tail(current, chunk_overlap)
            current = f"{current}\n\n{part}".strip() if current else part

    if current:
        chunks.append(current)

    return chunks


def split_long_text(text: str, *, chunk_size: int) -> list[str]:
    words = text.split()
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for word in words:
        next_len = current_len + len(word) + (1 if current else 0)
        if current and next_len > chunk_size:
            parts.append(" ".join(current))
            current = [word]
            current_len = len(word)
            continue
        current.append(word)
        current_len = next_len

    if current:
        parts.append(" ".join(current))

    return parts


def overlap_tail(text: str, chunk_overlap: int) -> str:
    if chunk_overlap <= 0:
        return ""
    return text[-chunk_overlap:].strip()


def embed_chunks(
    *,
    chunks: list[ChunkDocument],
    settings: Settings,
) -> list[list[float]]:
    vectors: list[list[float]] = []

    with httpx.Client(timeout=settings.tei_timeout_seconds) as client:
        for chunk in chunks:
            response = client.post(
                f"{settings.tei_url.rstrip('/')}/embed",
                json={"inputs": chunk.content},
            )
            response.raise_for_status()
            data = response.json()
            vectors.append(parse_tei_single_vector(data, settings=settings))

    if len(vectors) != len(chunks):
        raise ValueError(f"Embedding count mismatch: {len(vectors)} != {len(chunks)}")

    return vectors


def parse_tei_single_vector(
    data: object,
    *,
    settings: Settings,
) -> list[float]:
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        raise ValueError(f"Invalid TEI response: {data!r}")

    vector = [float(value) for value in data[0]]

    if len(vector) != settings.embedding_dimensions:
        raise ValueError(
            f"Embedding dimension mismatch: {len(vector)} != {settings.embedding_dimensions}"
        )

    return vector


def chunk_to_document(
    *,
    chunk: ChunkDocument,
    vector: list[float],
) -> dict[str, Any]:
    source = chunk.source
    metadata = source.metadata
    service_category = required_metadata(source, "service_category")
    section = required_metadata(source, "section")
    source_id = required_metadata(source, "source_id")
    title = required_metadata(source, "title")
    content_version = required_metadata(source, "content_version")

    return {
        "chunk_id": chunk.chunk_id,
        "content": chunk.content,
        "content_vector": vector,
        "checksum": chunk.checksum,
        "allowed_for_response": parse_bool(metadata.get("allowed_for_response"), True),
        "source_id": source_id,
        "source_type": metadata.get("source_type") or "bookcraft_knowledge",
        "title": title,
        "service_category": ServiceCategory(service_category).value,
        "subservice": metadata.get("subservice") or None,
        "audience": metadata.get("audience") or None,
        "funnel_stage": parse_funnel_stage(metadata.get("funnel_stage")),
        "section": section,
        "source_filename": source.relative_path,
        "tags": parse_tags(metadata.get("tags")),
        "content_version": content_version,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def elasticsearch_mapping(embedding_dimensions: int) -> dict[str, Any]:
    return {
        "settings": {
            "index": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "analysis": {
                "analyzer": {
                    "bookcraft_text": {
                        "type": "standard",
                    }
                }
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "chunk_id": {"type": "keyword"},
                "content": {"type": "text", "analyzer": "bookcraft_text"},
                "content_vector": {
                    "type": "dense_vector",
                    "dims": embedding_dimensions,
                    "index": False,
                },
                "checksum": {"type": "keyword"},
                "allowed_for_response": {"type": "boolean"},
                "source_id": {"type": "keyword"},
                "source_type": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "service_category": {"type": "keyword"},
                "subservice": {"type": "keyword"},
                "audience": {"type": "keyword"},
                "funnel_stage": {"type": "keyword"},
                "section": {"type": "keyword"},
                "source_filename": {"type": "keyword"},
                "tags": {"type": "keyword"},
                "content_version": {"type": "keyword"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
            },
        },
    }


def swap_index_alias(
    *,
    client: Elasticsearch,
    alias: str,
    new_index: str,
) -> None:
    actions: list[dict[str, Any]] = []

    try:
        existing = client.indices.get_alias(name=alias)
        for index_name in existing:
            actions.append({"remove": {"index": index_name, "alias": alias}})
    except NotFoundError:
        pass

    actions.append({"add": {"index": new_index, "alias": alias}})
    client.indices.update_aliases(actions=actions)


def elasticsearch_client(settings: Settings) -> Elasticsearch:
    kwargs: dict[str, Any] = {"request_timeout": 30}
    if settings.elasticsearch_user and settings.elasticsearch_password:
        kwargs["basic_auth"] = (
            settings.elasticsearch_user,
            settings.elasticsearch_password,
        )
    return Elasticsearch(settings.elasticsearch_url, **kwargs)


def required_metadata(source: SourceDocument, key: str) -> str:
    value = source.metadata.get(key)
    if not value:
        raise ValueError(f"Missing required metadata `{key}` in {source.relative_path}")
    return value


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() == "true"


def parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    cleaned = value.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    return [item.strip() for item in cleaned.split(",") if item.strip()]


def parse_funnel_stage(value: str | None) -> str | None:
    if not value:
        return None
    return SalesStage(value).value


def versioned_index_name(settings: Settings) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    prefix = settings.rag_index_version.rstrip("_")
    return f"{prefix}_{timestamp}"


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RAG Elasticsearch Index Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Apply: `{summary['apply']}`",
        f"- Swap alias requested: `{summary['swap_alias_requested']}`",
        f"- Alias swapped: `{summary['alias_swapped']}`",
        f"- Source files: `{summary['source_file_count']}`",
        f"- Chunks: `{summary['chunk_count']}`",
        f"- Indexed: `{summary['indexed_count']}`",
        f"- Index name: `{summary['index_name']}`",
        f"- Alias: `{summary['rag_index_alias']}`",
        f"- Embedding dimensions: `{summary['embedding_dimensions']}`",
        f"- Errors: `{len(summary['errors'])}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Sources",
        "",
        "| File | Source ID | Service | Section | Version |",
        "|---|---|---|---|---|",
    ]

    for source in report["sources"]:
        lines.append(
            "| `{path}` | `{source_id}` | `{service}` | `{section}` | `{version}` |".format(
                path=source["path"],
                source_id=source["source_id"],
                service=source["service_category"],
                section=source["section"],
                version=source["content_version"],
            )
        )

    lines.extend(
        [
            "",
            "## Errors",
            "",
        ]
    )

    if summary["errors"]:
        for error in summary["errors"]:
            lines.append(f"- `{error}`")
    else:
        lines.append("- none")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
