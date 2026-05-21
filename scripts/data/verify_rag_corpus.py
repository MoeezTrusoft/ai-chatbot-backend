from pathlib import Path

from bookcraft.components.rag.verifier import RagVerifier
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    report = RagVerifier(strict=settings.rag_strict_verifier).verify_build_dir(
        Path(settings.rag_build_dir)
    )
    print(
        f"rag verifier {report.verifier_status}: "
        f"{report.accepted_count} accepted, {report.rejected_count} rejected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
