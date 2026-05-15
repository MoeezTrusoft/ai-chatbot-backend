import json
from pathlib import Path

import pytest

from bookcraft.components.rag.schemas import RagChunk, RagChunkMetadata
from bookcraft.components.rag.verifier import RagVerifier


def write_build(tmp_path: Path, content: str) -> Path:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    chunk = RagChunk(
        chunk_id="chunk-1",
        content=content,
        metadata=RagChunkMetadata(
            source_id="source",
            title="Title",
            section="Section",
            source_filename="source.md",
            content_version="test",
        ),
        checksum="checksum",
    )
    (build_dir / "chunks.json").write_text(
        json.dumps([chunk.model_dump(mode="json")]),
        encoding="utf-8",
    )
    (build_dir / "source_checksums.json").write_text('{"source.md":"abc"}', encoding="utf-8")
    return build_dir


@pytest.mark.parametrize(
    "content",
    [
        "This service costs $100.",
        "We offer a 10% discount.",
        "Delivery is 4-8 weeks.",
        "Delivery takes 7 business days.",
        "Pricing is per word.",
        "Audiobook cost uses PFH.",
    ],
)
def test_rag_verifier_rejects_pricing_timeline_leakage(tmp_path: Path, content: str) -> None:
    build_dir = write_build(tmp_path, content)

    with pytest.raises(ValueError):
        RagVerifier(strict=True).verify_build_dir(build_dir)

    report = json.loads((build_dir / "rejected_chunks_report.json").read_text())
    assert report["rejected_count"] == 1


def test_rag_verifier_accepts_clean_service_description(tmp_path: Path) -> None:
    build_dir = write_build(tmp_path, "BookCraft explains the editing process and service fit.")

    report = RagVerifier(strict=True).verify_build_dir(build_dir)

    assert report.verifier_status == "passed"
    assert report.rejected_count == 0
