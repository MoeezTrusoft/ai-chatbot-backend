from pathlib import Path

from bookcraft.components.rag.pipeline import build_chunks, write_build_artifacts
from bookcraft.components.rag.verifier import RagVerifier
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    chunks, checksums = build_chunks(
        source_dir=Path(settings.rag_source_dir),
        max_tokens=settings.rag_max_tokens_per_chunk,
        overlap_tokens=settings.rag_chunk_overlap_tokens,
    )
    verifier = RagVerifier(strict=False)
    safe_chunks = [chunk for chunk in chunks if verifier._first_forbidden(chunk.content) is None]
    write_build_artifacts(
        chunks=safe_chunks,
        source_checksums=checksums,
        build_dir=Path(settings.rag_build_dir),
    )
    quarantined = len(chunks) - len(safe_chunks)
    print(
        f"built {len(safe_chunks)} RAG chunks in {settings.rag_build_dir}; "
        f"quarantined {quarantined} unsafe chunks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
