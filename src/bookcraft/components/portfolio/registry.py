from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bookcraft.domain.enums import ServiceCategory

from .schemas import PortfolioMediaType, PortfolioSample

REGISTRY_VERSION = "portfolio_registry_v1"

SERVICE_REGISTRY_KEYS: dict[ServiceCategory, tuple[str, ...]] = {
    ServiceCategory.COVER_DESIGN_ILLUSTRATION: ("Cover Design & Illustrations",),
    ServiceCategory.PUBLISHING_DISTRIBUTION: ("Publishing",),
    ServiceCategory.EDITING_PROOFREADING: ("Editing & Proofreading",),
    ServiceCategory.INTERIOR_FORMATTING: ("Formatting",),
    ServiceCategory.MARKETING_PROMOTION: ("Publishing",),
    ServiceCategory.VIDEO_TRAILER: ("Video Trailer",),
}


class PortfolioRegistry:
    def __init__(
        self,
        samples: dict[ServiceCategory, dict[str, list[PortfolioSample]]],
        genre_aliases: dict[str, set[str]] | None = None,
        *,
        version: str = REGISTRY_VERSION,
    ) -> None:
        self.samples = samples
        self.genre_aliases = genre_aliases or {}
        self.version = version

    @classmethod
    def from_files(
        cls,
        *,
        samples_registry_path: str | Path,
        genre_hierarchy_path: str | Path | None = None,
        portfolio_docx_path: str | Path | None = None,
    ) -> PortfolioRegistry:
        raw_registry = load_samples_registry_js(Path(samples_registry_path))
        genre_aliases = (
            load_genre_aliases(Path(genre_hierarchy_path)) if genre_hierarchy_path else {}
        )
        docx_titles = (
            load_docx_titles(Path(portfolio_docx_path)) if portfolio_docx_path else set()
        )
        samples = normalize_registry(raw_registry, docx_titles=docx_titles)
        return cls(samples=samples, genre_aliases=genre_aliases)

    def for_service(self, service: ServiceCategory) -> dict[str, list[PortfolioSample]]:
        return self.samples.get(service, {})

    def candidate_genres(self, genre: str | None) -> list[str]:
        if not genre:
            return ["default"]
        normalized = normalize_key(genre)
        candidates = [normalized]
        candidates.extend(sorted(self.genre_aliases.get(normalized, set())))
        candidates.append("default")
        return list(dict.fromkeys(candidates))


def load_samples_registry_js(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"export\s+const\s+SAMPLES_REGISTRY\s*=\s*(\{.*?\})\s*;\s*export\s+function",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        raise ValueError(f"Could not locate SAMPLES_REGISTRY in {path}")
    object_text = match.group(1)
    object_text = re.sub(r"(?m)(\s*)(byGenre)\s*:", r'\1"\2":', object_text)
    loaded = json.loads(object_text)
    if not isinstance(loaded, dict):
        raise ValueError("SAMPLES_REGISTRY must be an object")
    return loaded


def load_genre_aliases(path: Path) -> dict[str, set[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    aliases: dict[str, set[str]] = {}

    def walk(node: Any, ancestors: list[str]) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key == "__items__":
                for item in value if isinstance(value, list) else []:
                    for genre in item.get("genres", []) if isinstance(item, dict) else []:
                        register_aliases(aliases, str(genre), ancestors)
                continue
            current = normalize_key(key)
            register_aliases(aliases, current, ancestors)
            walk(value, [*ancestors, current])

    walk(data, [])
    return aliases


def register_aliases(aliases: dict[str, set[str]], key: str, related: Iterable[str]) -> None:
    normalized = normalize_key(key)
    if not normalized:
        return
    bucket = aliases.setdefault(normalized, set())
    bucket.update(normalize_key(item) for item in related if normalize_key(item))


def load_docx_titles(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with zipfile.ZipFile(path) as archive:
        try:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
        except KeyError:
            return set()
    text = re.sub(r"<[^>]+>", " ", xml)
    return {line.strip() for line in re.split(r"\s{2,}", text) if line.strip()}


def normalize_registry(
    raw_registry: dict[str, Any],
    *,
    docx_titles: set[str] | None = None,
) -> dict[ServiceCategory, dict[str, list[PortfolioSample]]]:
    del docx_titles
    normalized: dict[ServiceCategory, dict[str, list[PortfolioSample]]] = {}
    for service, registry_keys in SERVICE_REGISTRY_KEYS.items():
        by_genre: dict[str, list[PortfolioSample]] = {}
        for registry_key in registry_keys:
            service_data = raw_registry.get(registry_key, {})
            raw_by_genre = service_data.get("byGenre", {}) if isinstance(service_data, dict) else {}
            if not isinstance(raw_by_genre, dict):
                continue
            for genre, records in raw_by_genre.items():
                if not isinstance(records, list):
                    continue
                genre_key = normalize_key(genre)
                by_genre.setdefault(genre_key, [])
                for index, record in enumerate(records):
                    sample = normalize_sample(
                        record=record,
                        service=service,
                        genre=genre_key,
                        source_id=f"{registry_key}:{genre_key}:{index}",
                    )
                    if sample is not None:
                        by_genre[genre_key].append(sample)
        normalized[service] = {
            genre: dedupe_samples(samples) for genre, samples in by_genre.items() if samples
        }
    return normalized


def normalize_sample(
    *,
    record: Any,
    service: ServiceCategory,
    genre: str,
    source_id: str,
) -> PortfolioSample | None:
    if not isinstance(record, dict):
        return None
    title = str(record.get("title") or "").strip()
    url = clean_optional_url(record.get("url"))
    cover = clean_optional_url(record.get("cover"))
    if not title or (not url and not cover):
        return None
    media_type = infer_media_type(service, url, cover)
    return PortfolioSample(
        title=title,
        service=service,
        genre=genre,
        url=url,
        cover_image=cover,
        media_type=media_type,
        reason_selected=reason_for_sample(service, genre),
        source_id=source_id,
    )


def clean_optional_url(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def infer_media_type(
    service: ServiceCategory,
    url: str | None,
    cover: str | None,
) -> PortfolioMediaType:
    if service == ServiceCategory.VIDEO_TRAILER:
        return PortfolioMediaType.VIDEO
    if service == ServiceCategory.COVER_DESIGN_ILLUSTRATION and cover and not url:
        return PortfolioMediaType.COVER_IMAGE
    if url and "amazon." in url.lower():
        return PortfolioMediaType.AMAZON_LINK
    if service == ServiceCategory.AUTHOR_WEBSITE:
        return PortfolioMediaType.WEBSITE
    return PortfolioMediaType.EXTERNAL_LINK if url else PortfolioMediaType.COVER_IMAGE


def reason_for_sample(service: ServiceCategory, genre: str) -> str:
    if service == ServiceCategory.MARKETING_PROMOTION:
        return "Registry-backed published-book example relevant to marketing and promotion."
    if genre == "default":
        return "Registry-backed fallback sample for this service."
    return f"Registry-backed sample matched to genre '{genre}'."


def dedupe_samples(samples: list[PortfolioSample]) -> list[PortfolioSample]:
    seen: set[tuple[str, str | None, str | None]] = set()
    deduped: list[PortfolioSample] = []
    for sample in samples:
        key = (sample.title.casefold(), sample.url, sample.cover_image)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sample)
    return deduped


def normalize_key(value: str) -> str:
    return " ".join(str(value).strip().lower().split())
