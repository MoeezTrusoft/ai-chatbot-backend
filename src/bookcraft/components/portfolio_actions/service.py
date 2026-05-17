from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from bookcraft.components.portfolio import PortfolioEngine
from bookcraft.components.portfolio.schemas import PortfolioRequest
from bookcraft.components.portfolio_actions.schemas import (
    PortfolioActionRequest,
    PortfolioActionResult,
)
from bookcraft.domain.enums import ServiceCategory


class PortfolioViewRepositoryProtocol(Protocol):
    async def list_seen_sample_ids(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        service_category: str | None = None,
    ) -> list[str]: ...

    async def save_views(
        self,
        *,
        customer_id: UUID | None,
        thread_id: UUID,
        service_category: str,
        genre: str | None,
        sample_ids: list[str],
    ) -> None: ...


@dataclass(slots=True)
class PortfolioActionService:
    portfolio_engine: PortfolioEngine
    repository: PortfolioViewRepositoryProtocol

    async def lookup(self, request: PortfolioActionRequest) -> PortfolioActionResult:
        service = ServiceCategory(request.service)
        seen_ids = await self.repository.list_seen_sample_ids(
            customer_id=request.customer_id,
            thread_id=request.thread_id,
            service_category=service.value,
        )
        exclude_ids = list(dict.fromkeys([*seen_ids, *request.exclude_sample_ids]))

        expanded_limit = min(max(request.limit + len(exclude_ids), request.limit), 10)
        response = self.portfolio_engine.request_samples(
            PortfolioRequest(
                service=service,
                genre=request.genre,
                limit=expanded_limit,
            )
        )

        samples = [
            sample for sample in response.samples if sample.source_id not in set(exclude_ids)
        ][: request.limit]

        sample_ids = [sample.source_id for sample in samples]
        await self.repository.save_views(
            customer_id=request.customer_id,
            thread_id=request.thread_id,
            service_category=service.value,
            genre=response.matched_genre or request.genre,
            sample_ids=sample_ids,
        )

        return PortfolioActionResult(
            service=service.value,
            requested_genre=response.requested_genre,
            status=response.status.value,
            message=response.message,
            samples=[sample.model_dump(mode="json") for sample in samples],
            sample_ids=sample_ids,
            skipped_sample_ids=exclude_ids,
            matched_genre=response.matched_genre,
            fallback_used=response.fallback_used,
            customer_safe_summary=_customer_safe_summary(
                status=response.status.value,
                service=service.value,
                sample_count=len(samples),
                skipped_count=len(exclude_ids),
            ),
        )


def _customer_safe_summary(
    *,
    status: str,
    service: str,
    sample_count: int,
    skipped_count: int,
) -> str:
    if status == "found" and sample_count > 0:
        if skipped_count:
            return (
                f"I found {sample_count} more approved sample(s) for {service}, "
                "skipping examples already shared in this thread."
            )
        return f"I found {sample_count} approved sample(s) for {service}."

    if status == "unavailable_confidential":
        return "I cannot share ghostwriting samples publicly because client work is confidential."

    if status == "unavailable_pending":
        return "Approved samples for this service are not available in the registry yet."

    return "I could not find approved samples that match this request."
