from bookcraft.components.portfolio.engine import PortfolioEngine
from bookcraft.components.portfolio.registry import PortfolioRegistry
from bookcraft.components.portfolio.schemas import (
    PortfolioMediaType,
    PortfolioRequest,
    PortfolioSample,
    PortfolioStatus,
)
from bookcraft.components.portfolio.verifier import PortfolioVerifier
from bookcraft.domain.enums import ServiceCategory


def test_portfolio_engine_filters_unsafe_links_before_returning_samples() -> None:
    registry = PortfolioRegistry(
        samples={
            ServiceCategory.COVER_DESIGN_ILLUSTRATION: {
                "fantasy": [
                    PortfolioSample(
                        title="Unsafe Internal Sample",
                        service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
                        genre="fantasy",
                        url="https://portfolio.internal/private",
                        cover_image=None,
                        media_type=PortfolioMediaType.EXTERNAL_LINK,
                        reason_selected="test",
                        source_id="unsafe:1",
                    ),
                    PortfolioSample(
                        title="Safe Sample",
                        service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
                        genre="fantasy",
                        url="https://bookcraftpublishers.com/portfolio/safe",
                        cover_image=None,
                        media_type=PortfolioMediaType.EXTERNAL_LINK,
                        reason_selected="test",
                        source_id="safe:1",
                    ),
                ]
            }
        }
    )

    response = PortfolioEngine(registry).request_samples(
        PortfolioRequest(
            service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
            genre="fantasy",
        )
    )

    assert response.status == PortfolioStatus.FOUND
    assert [sample.title for sample in response.samples] == ["Safe Sample"]


def test_portfolio_engine_returns_no_match_when_only_unsafe_links_exist() -> None:
    registry = PortfolioRegistry(
        samples={
            ServiceCategory.COVER_DESIGN_ILLUSTRATION: {
                "fantasy": [
                    PortfolioSample(
                        title="Unsafe Internal Sample",
                        service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
                        genre="fantasy",
                        url="https://portfolio.internal/private",
                        cover_image=None,
                        media_type=PortfolioMediaType.EXTERNAL_LINK,
                        reason_selected="test",
                        source_id="unsafe:1",
                    )
                ]
            }
        }
    )

    response = PortfolioEngine(registry).request_samples(
        PortfolioRequest(
            service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
            genre="fantasy",
        )
    )

    assert response.status == PortfolioStatus.NO_MATCH
    assert response.samples == []


def test_portfolio_verifier_rejects_unsafe_links() -> None:
    registry = PortfolioRegistry(
        samples={
            ServiceCategory.COVER_DESIGN_ILLUSTRATION: {
                "fantasy": [
                    PortfolioSample(
                        title="Unsafe Internal Sample",
                        service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
                        genre="fantasy",
                        url="https://portfolio.internal/private",
                        cover_image=None,
                        media_type=PortfolioMediaType.EXTERNAL_LINK,
                        reason_selected="test",
                        source_id="unsafe:1",
                    )
                ]
            }
        }
    )

    result = PortfolioVerifier().verify(registry)

    assert not result.valid
    assert any("unsafe url" in error for error in result.errors)
