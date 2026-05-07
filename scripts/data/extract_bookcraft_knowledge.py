from pathlib import Path

from bookcraft.components.rag.pipeline import SOURCE_ROOT, extract_source_markdown
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    checksums = extract_source_markdown(SOURCE_ROOT, Path(settings.rag_source_dir))
    print(f"copied {len(checksums)} markdown sources to {settings.rag_source_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

