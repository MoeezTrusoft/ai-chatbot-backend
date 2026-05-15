from pathlib import Path

from bookcraft.components.rag.pipeline import (
    build_chunks,
    normalize_markdown,
    split_sections,
    token_chunks,
)
from bookcraft.domain.enums import ServiceCategory


def test_markdown_section_extraction_and_chunk_overlap() -> None:
    sections = split_sections("# Title\n\nIntro text\n\n## Process\n\none two three four five six")
    chunks = token_chunks("one two three four five six", max_tokens=4, overlap_tokens=2)

    assert [section.title for section in sections] == ["Title", "Process"]
    assert chunks == ["one two three four", "three four five six"]


def test_normalize_markdown_is_deterministic() -> None:
    assert normalize_markdown("A  **bold**\r\n\r\n\r\nB") == "A bold\n\nB"


def test_build_chunks_infers_service_metadata_and_stable_ids(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "ghostwriting.md").write_text(
        "# Ghostwriting\n\nWe help authors shape books.\n\n## Process\n\nDiscovery and drafting.",
        encoding="utf-8",
    )
    (source_dir / "manifest.json").write_text(
        '{"version":"test","documents":[{"filename":"ghostwriting.md","title":"Ghostwriting"}]}',
        encoding="utf-8",
    )

    left, _ = build_chunks(source_dir=source_dir, max_tokens=20, overlap_tokens=5)
    right, _ = build_chunks(source_dir=source_dir, max_tokens=20, overlap_tokens=5)

    assert left[0].chunk_id == right[0].chunk_id
    assert left[0].checksum == right[0].checksum
    assert left[0].metadata.service_category == ServiceCategory.GHOSTWRITING
