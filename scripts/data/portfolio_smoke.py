from __future__ import annotations

import json
from pathlib import Path

from bookcraft.components.portfolio import PortfolioEngine, PortfolioRegistry, PortfolioRequest
from bookcraft.domain.enums import ServiceCategory
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    registry = PortfolioRegistry.from_files(
        samples_registry_path=Path(settings.portfolio_samples_registry_path),
        genre_hierarchy_path=Path(settings.portfolio_genre_hierarchy_path),
        portfolio_docx_path=Path(settings.portfolio_samples_docx_path),
    )
    engine = PortfolioEngine(registry)
    checks = [
        PortfolioRequest(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION, genre="cozy mystery"),
        PortfolioRequest(service=ServiceCategory.PUBLISHING_DISTRIBUTION, genre="cozy mystery"),
        PortfolioRequest(service=ServiceCategory.EDITING_PROOFREADING, genre="cozy mystery"),
        PortfolioRequest(service=ServiceCategory.INTERIOR_FORMATTING, genre="cozy mystery"),
        PortfolioRequest(service=ServiceCategory.MARKETING_PROMOTION, genre="cozy mystery"),
        PortfolioRequest(service=ServiceCategory.VIDEO_TRAILER),
        PortfolioRequest(service=ServiceCategory.GHOSTWRITING),
        PortfolioRequest(service=ServiceCategory.AUDIOBOOK_PRODUCTION),
        PortfolioRequest(service=ServiceCategory.AUTHOR_WEBSITE),
    ]
    responses = [engine.request_samples(request).model_dump(mode="json") for request in checks]
    print(json.dumps(responses, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
