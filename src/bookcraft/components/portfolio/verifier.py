from __future__ import annotations

from collections import Counter

from bookcraft.domain.enums import ServiceCategory

from .registry import PortfolioRegistry
from .safety import unsafe_portfolio_url_reason
from .schemas import PortfolioVerificationResult


class PortfolioVerifier:
    def verify(self, registry: PortfolioRegistry) -> PortfolioVerificationResult:
        errors: list[str] = []
        warnings: list[str] = []
        service_counts: Counter[str] = Counter()
        sample_count = 0

        for service, by_genre in registry.samples.items():
            for genre, samples in by_genre.items():
                if not samples:
                    warnings.append(f"{service.value}:{genre}:empty genre bucket")
                for sample in samples:
                    sample_count += 1
                    service_counts[service.value] += 1
                    if sample.service != service:
                        errors.append(f"{sample.source_id}: service mismatch")
                    if not sample.title.strip():
                        errors.append(f"{sample.source_id}: missing title")
                    if not sample.url and not sample.cover_image:
                        errors.append(f"{sample.source_id}: missing url and cover_image")
                    url_reason = unsafe_portfolio_url_reason(sample.url)
                    if url_reason:
                        errors.append(f"{sample.source_id}: unsafe url: {url_reason}")
                    cover_reason = unsafe_portfolio_url_reason(sample.cover_image)
                    if cover_reason:
                        errors.append(f"{sample.source_id}: unsafe cover_image: {cover_reason}")

        required_with_samples = {
            ServiceCategory.COVER_DESIGN_ILLUSTRATION,
            ServiceCategory.PUBLISHING_DISTRIBUTION,
            ServiceCategory.EDITING_PROOFREADING,
            ServiceCategory.INTERIOR_FORMATTING,
            ServiceCategory.MARKETING_PROMOTION,
            ServiceCategory.VIDEO_TRAILER,
        }
        for service in required_with_samples:
            if service_counts[service.value] == 0:
                errors.append(f"{service.value}: no approved samples loaded")

        return PortfolioVerificationResult(
            valid=not errors,
            sample_count=sample_count,
            service_counts=dict(sorted(service_counts.items())),
            errors=errors,
            warnings=warnings,
        )
