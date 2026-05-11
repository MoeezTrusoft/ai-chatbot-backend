from __future__ import annotations

from prometheus_client import Counter

from bookcraft.domain.enums import ServiceCategory

from .registry import PortfolioRegistry
from .safety import is_safe_portfolio_url
from .schemas import PortfolioRequest, PortfolioResponse, PortfolioStatus

PORTFOLIO_REQUESTS = Counter(
    "portfolio_requests_total",
    "Portfolio requests by service and status.",
    ["service", "status"],
)


class PortfolioEngine:
    def __init__(self, registry: PortfolioRegistry) -> None:
        self.registry = registry

    def request_samples(self, request: PortfolioRequest) -> PortfolioResponse:
        if request.service == ServiceCategory.GHOSTWRITING:
            response = PortfolioResponse(
                service=request.service,
                requested_genre=request.genre,
                status=PortfolioStatus.UNAVAILABLE_CONFIDENTIAL,
                message="Ghostwriting samples are not shared because client work is confidential.",
                registry_version=self.registry.version,
            )
            self._record(response)
            return response
        if request.service == ServiceCategory.AUDIOBOOK_PRODUCTION:
            response = PortfolioResponse(
                service=request.service,
                requested_genre=request.genre,
                status=PortfolioStatus.UNAVAILABLE_PENDING,
                message="Audiobook samples are not available in the approved registry yet.",
                registry_version=self.registry.version,
            )
            self._record(response)
            return response
        if request.service == ServiceCategory.AUTHOR_WEBSITE:
            response = PortfolioResponse(
                service=request.service,
                requested_genre=request.genre,
                status=PortfolioStatus.UNAVAILABLE_PENDING,
                message="Author website samples are not available in the approved registry yet.",
                registry_version=self.registry.version,
            )
            self._record(response)
            return response

        by_genre = self.registry.for_service(request.service)
        for candidate in self.registry.candidate_genres(request.genre):
            samples = [
                sample
                for sample in by_genre.get(candidate, [])
                if is_safe_portfolio_url(sample.url) and is_safe_portfolio_url(sample.cover_image)
            ]
            if samples:
                response = PortfolioResponse(
                    service=request.service,
                    requested_genre=request.genre,
                    status=PortfolioStatus.FOUND,
                    samples=samples[: request.limit],
                    message="Returned approved registry samples only.",
                    registry_version=self.registry.version,
                    matched_genre=candidate,
                    fallback_used=candidate == "default" and bool(request.genre),
                )
                self._record(response)
                return response
        response = PortfolioResponse(
            service=request.service,
            requested_genre=request.genre,
            status=PortfolioStatus.NO_MATCH,
            samples=[],
            message="No approved registry samples matched this request.",
            registry_version=self.registry.version,
        )
        self._record(response)
        return response

    def _record(self, response: PortfolioResponse) -> None:
        PORTFOLIO_REQUESTS.labels(
            service=response.service.value,
            status=response.status.value,
        ).inc()
