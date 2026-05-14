from pathlib import Path

from bookcraft.components.portfolio import (
    PortfolioEngine,
    PortfolioRegistry,
    PortfolioRequest,
    PortfolioStatus,
    PortfolioVerifier,
)
from bookcraft.domain.enums import ServiceCategory


def _engine() -> PortfolioEngine:
    return PortfolioEngine(
        PortfolioRegistry.from_files(
            samples_registry_path=Path("data/portfolio/samples.registry.js"),
            genre_hierarchy_path=Path("data/portfolio/genre_hierarchy_links.json"),
            portfolio_docx_path=Path("data/portfolio/portfolio_samples.docx"),
        )
    )


def test_portfolio_verifier_accepts_canonical_registry() -> None:
    registry = PortfolioRegistry.from_files(
        samples_registry_path=Path("data/portfolio/samples.registry.js"),
        genre_hierarchy_path=Path("data/portfolio/genre_hierarchy_links.json"),
        portfolio_docx_path=Path("data/portfolio/portfolio_samples.docx"),
    )

    result = PortfolioVerifier().verify(registry)

    assert result.valid is True
    assert result.sample_count > 0
    assert result.service_counts["cover_design_illustration"] > 0
    assert result.service_counts["video_trailer"] > 0


def test_request_cover_samples_by_genre() -> None:
    response = _engine().request_samples(
        PortfolioRequest(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION, genre="cozy mystery")
    )

    assert response.status == PortfolioStatus.FOUND
    assert response.samples
    assert all(
        sample.service == ServiceCategory.COVER_DESIGN_ILLUSTRATION for sample in response.samples
    )
    assert all(sample.cover_image or sample.url for sample in response.samples)


def test_request_publishing_samples_by_genre_from_registry_only() -> None:
    response = _engine().request_samples(
        PortfolioRequest(service=ServiceCategory.PUBLISHING_DISTRIBUTION, genre="cozy mystery")
    )

    assert response.status == PortfolioStatus.FOUND
    assert response.samples
    assert all(sample.url or sample.cover_image for sample in response.samples)
    assert all(sample.source_id.startswith("Publishing:") for sample in response.samples)


def test_request_marketing_uses_published_book_registry_mapping() -> None:
    response = _engine().request_samples(
        PortfolioRequest(service=ServiceCategory.MARKETING_PROMOTION, genre="cozy mystery")
    )

    assert response.status == PortfolioStatus.FOUND
    assert response.samples
    assert all(sample.service == ServiceCategory.MARKETING_PROMOTION for sample in response.samples)
    assert all(sample.source_id.startswith("Publishing:") for sample in response.samples)


def test_request_video_trailer_samples() -> None:
    response = _engine().request_samples(
        PortfolioRequest(service=ServiceCategory.VIDEO_TRAILER, limit=4)
    )

    assert response.status == PortfolioStatus.FOUND
    assert len(response.samples) == 4
    assert all(str(sample.url).endswith(".mp4") for sample in response.samples)


def test_ghostwriting_samples_are_confidential() -> None:
    response = _engine().request_samples(PortfolioRequest(service=ServiceCategory.GHOSTWRITING))

    assert response.status == PortfolioStatus.UNAVAILABLE_CONFIDENTIAL
    assert response.samples == []


def test_audiobook_samples_are_pending() -> None:
    response = _engine().request_samples(
        PortfolioRequest(service=ServiceCategory.AUDIOBOOK_PRODUCTION)
    )

    assert response.status == PortfolioStatus.UNAVAILABLE_PENDING
    assert response.samples == []


def test_author_website_samples_are_pending_without_hallucinated_links() -> None:
    response = _engine().request_samples(PortfolioRequest(service=ServiceCategory.AUTHOR_WEBSITE))

    assert response.status == PortfolioStatus.UNAVAILABLE_PENDING
    assert response.samples == []


def test_unknown_genre_falls_back_to_registry_default() -> None:
    response = _engine().request_samples(
        PortfolioRequest(service=ServiceCategory.EDITING_PROOFREADING, genre="not a real genre")
    )

    assert response.status == PortfolioStatus.FOUND
    assert response.fallback_used is True
    assert response.matched_genre == "default"
