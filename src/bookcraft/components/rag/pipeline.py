from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from bookcraft.components.rag.schemas import RagChunk, RagChunkMetadata
from bookcraft.domain.enums import SalesStage, ServiceCategory

SOURCE_ROOT = Path("docs/bookcraft_knowledge/enhanced_content_v2")

SERVICE_BY_FILE = {
    "ghostwriting.md": ServiceCategory.GHOSTWRITING,
    "editing-proofreading.md": ServiceCategory.EDITING_PROOFREADING,
    "cover-design-illustration.md": ServiceCategory.COVER_DESIGN_ILLUSTRATION,
    "formatting.md": ServiceCategory.INTERIOR_FORMATTING,
    "audiobook-production.md": ServiceCategory.AUDIOBOOK_PRODUCTION,
    "publishing-distribution.md": ServiceCategory.PUBLISHING_DISTRIBUTION,
    "marketing-promotion.md": ServiceCategory.MARKETING_PROMOTION,
    "authors-website.md": ServiceCategory.AUTHOR_WEBSITE,
    "video-trailers.md": ServiceCategory.VIDEO_TRAILER,
    # New services — 3 files each (description, process, FAQ)
    "fine-art-monograph-publishing-service-description.md": (
        ServiceCategory.FINE_ART_MONOGRAPH
    ),
    "our-process-for-fine-art-monograph-publishing.md": ServiceCategory.FINE_ART_MONOGRAPH,
    "faq-fine-art-monograph-publishing.md": ServiceCategory.FINE_ART_MONOGRAPH,
    "catalog-transition-and-rights-recovery-service-description.md": (
        ServiceCategory.CATALOG_TRANSITION
    ),
    "our-process-for-catalog-transition-and-rights-recovery.md": (
        ServiceCategory.CATALOG_TRANSITION
    ),
    "faq-catalog-transition-and-rights-recovery.md": ServiceCategory.CATALOG_TRANSITION,
    "full-service-and-hybrid-publishing-partnership-service-description.md": (
        ServiceCategory.PUBLISHING_PARTNERSHIP
    ),
    "our-process-for-full-service-and-hybrid-publishing-partnership.md": (
        ServiceCategory.PUBLISHING_PARTNERSHIP
    ),
    "faq-full-service-and-hybrid-publishing-partnership.md": (
        ServiceCategory.PUBLISHING_PARTNERSHIP
    ),
    "author-brand-and-platform-strategy-service-description.md": (
        ServiceCategory.AUTHOR_BRAND_PLATFORM
    ),
    "our-process-for-author-brand-and-platform-strategy.md": (
        ServiceCategory.AUTHOR_BRAND_PLATFORM
    ),
    "faq-author-brand-and-platform-strategy.md": ServiceCategory.AUTHOR_BRAND_PLATFORM,
    "translation-and-foreign-rights-service-description.md": (
        ServiceCategory.TRANSLATION_FOREIGN_RIGHTS
    ),
    "our-process-for-translation-and-foreign-rights.md": (
        ServiceCategory.TRANSLATION_FOREIGN_RIGHTS
    ),
    "faq-translation-and-foreign-rights.md": ServiceCategory.TRANSLATION_FOREIGN_RIGHTS,
    "special-and-collector-editions-service-description.md": (
        ServiceCategory.SPECIAL_COLLECTOR_EDITIONS
    ),
    "our-process-for-special-and-collector-editions.md": (
        ServiceCategory.SPECIAL_COLLECTOR_EDITIONS
    ),
    "faq-special-and-collector-editions.md": ServiceCategory.SPECIAL_COLLECTOR_EDITIONS,
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_source_markdown(source_root: Path, output_dir: Path) -> dict[str, str]:
    markdown_dir = source_root / "markdown"
    manifest_path = source_root / "manifest.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    for source in sorted(markdown_dir.glob("*.md")):
        target = output_dir / source.name
        shutil.copyfile(source, target)
        checksums[source.name] = sha256_text(target.read_text(encoding="utf-8"))
    if manifest_path.exists():
        shutil.copyfile(manifest_path, output_dir / "manifest.json")
    return checksums


def load_manifest(source_dir: Path) -> dict[str, object]:
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        return {"version": "unknown", "documents": []}
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = "RAG manifest must be a JSON object."
        raise ValueError(msg)
    return loaded


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    title: str
    content: str


def normalize_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    return text.strip()


def split_sections(markdown: str) -> list[MarkdownSection]:
    normalized = normalize_markdown(markdown)
    matches = list(re.finditer(r"^(#{1,3})\s+(.+)$", normalized, flags=re.M))
    if not matches:
        return [MarkdownSection(title="Overview", content=normalized)]
    sections: list[MarkdownSection] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        content = normalized[start:end].strip()
        if content:
            sections.append(MarkdownSection(title=match.group(2).strip(), content=content))
    return sections


def token_chunks(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    tokens = text.split()
    if not tokens:
        return []
    chunks: list[str] = []
    step = max(1, max_tokens - overlap_tokens)
    for start in range(0, len(tokens), step):
        chunk_tokens = tokens[start : start + max_tokens]
        chunks.append(" ".join(chunk_tokens))
        if start + max_tokens >= len(tokens):
            break
    return chunks


def build_chunks(
    *,
    source_dir: Path,
    max_tokens: int,
    overlap_tokens: int,
) -> tuple[list[RagChunk], dict[str, str]]:
    manifest = load_manifest(source_dir)
    version = str(manifest.get("version", "unknown"))
    chunks: list[RagChunk] = []
    checksums: dict[str, str] = {}
    for path in sorted(source_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        source_checksum = sha256_text(raw)
        checksums[path.name] = source_checksum
        title = _title_for(path.name, manifest)
        service = SERVICE_BY_FILE.get(path.name)
        for section in split_sections(raw):
            section_text = f"{section.title}\n\n{section.content}"
            for chunk_index, content in enumerate(
                token_chunks(section_text, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
            ):
                source_id = path.stem
                chunk_id = _chunk_id(source_id, section.title, chunk_index, content)
                checksum = sha256_text(content)
                chunks.append(
                    RagChunk(
                        chunk_id=chunk_id,
                        content=content,
                        metadata=RagChunkMetadata(
                            source_id=source_id,
                            title=title,
                            service_category=service,
                            audience="authors",
                            funnel_stage=SalesStage.SERVICE_DISCOVERY,
                            section=section.title,
                            source_filename=path.name,
                            tags=_tags_for(path.name, section.title),
                            content_version=version,
                        ),
                        checksum=checksum,
                    )
                )
    return chunks, checksums


def write_build_artifacts(
    *,
    chunks: list[RagChunk],
    source_checksums: dict[str, str],
    build_dir: Path,
) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    payload = [chunk.model_dump(mode="json") for chunk in chunks]
    (build_dir / "chunks.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (build_dir / "source_checksums.json").write_text(
        json.dumps(source_checksums, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_chunks(build_dir: Path) -> list[RagChunk]:
    path = build_dir / "chunks.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        msg = "chunks.json must contain a list."
        raise ValueError(msg)
    return [RagChunk.model_validate(item) for item in loaded]


def _title_for(filename: str, manifest: dict[str, object]) -> str:
    documents = manifest.get("documents")
    if isinstance(documents, list):
        for item in documents:
            if isinstance(item, dict) and item.get("filename") == filename:
                return str(item.get("title", filename))
    return filename.removesuffix(".md").replace("-", " ").title()


def _tags_for(filename: str, section: str) -> list[str]:
    tags = [filename.removesuffix(".md"), section.lower().replace(" ", "_")]
    if "question" in section.lower():
        tags.append("faq")
    if "process" in section.lower():
        tags.append("process")
    return tags


def _chunk_id(source_id: str, section: str, chunk_index: int, content: str) -> str:
    raw = f"{source_id}:{section}:{chunk_index}:{sha256_text(content)[:12]}"
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "-", raw).lower()
