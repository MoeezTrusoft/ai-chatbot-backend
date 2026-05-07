from pathlib import Path

from bookcraft.components.rag.pipeline import build_chunks, write_build_artifacts
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    chunks, checksums = build_chunks(
        source_dir=Path(settings.rag_source_dir),
        max_tokens=settings.rag_max_tokens_per_chunk,
        overlap_tokens=settings.rag_chunk_overlap_tokens,
    )
    write_build_artifacts(
        chunks=chunks,
        source_checksums=checksums,
        build_dir=Path(settings.rag_build_dir),
    )
    print(f"built {len(chunks)} RAG chunks in {settings.rag_build_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

