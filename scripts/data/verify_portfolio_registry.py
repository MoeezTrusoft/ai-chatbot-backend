from __future__ import annotations

import json
from pathlib import Path

from bookcraft.components.portfolio import PortfolioRegistry, PortfolioVerifier
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    registry = PortfolioRegistry.from_files(
        samples_registry_path=Path(settings.portfolio_samples_registry_path),
        genre_hierarchy_path=Path(settings.portfolio_genre_hierarchy_path),
        portfolio_docx_path=Path(settings.portfolio_samples_docx_path),
    )
    result = PortfolioVerifier().verify(registry)
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if not result.valid:
        return 1
    print("portfolio verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
