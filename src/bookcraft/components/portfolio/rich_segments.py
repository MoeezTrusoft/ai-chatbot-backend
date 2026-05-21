"""PortfolioRichSegmentBuilder — converts portfolio samples into rich segment dicts.

Portfolio URLs must NOT appear as raw text in assistant messages.
Instead, they are sent as typed rich segments that the frontend renders
as underlined clickable links.

Segment schemas:

Single sample:
  {"type": "portfolio_link", "title": "...", "url": "https://...", "service": "..."}

Multiple samples:
  {"type": "portfolio_links", "items": [{"title": ..., "url": ..., "service": ...}, ...]}
"""

from __future__ import annotations

from typing import Any

from bookcraft.components.portfolio.link_sanitizer import PortfolioLinkSanitizer


class PortfolioRichSegmentBuilder:
    """Builds rich segment dicts from a portfolio response object."""

    def __init__(self) -> None:
        self._sanitizer = PortfolioLinkSanitizer()

    def build(self, portfolio_response: Any) -> list[dict[str, Any]]:
        """Return rich segments for all samples in a portfolio response.

        Returns an empty list when there are no valid samples.
        """
        samples = getattr(portfolio_response, "samples", None) or []
        if not samples:
            return []

        links = self._sanitizer.sanitize(list(samples))
        if not links:
            return []

        if len(links) == 1:
            link = links[0]
            seg: dict[str, Any] = {
                "type": "portfolio_link",
                "title": link.title,
                "url": link.url,
            }
            if link.service:
                seg["service"] = link.service
            return [seg]

        items: list[dict[str, Any]] = []
        for link in links:
            item: dict[str, Any] = {"title": link.title, "url": link.url}
            if link.service:
                item["service"] = link.service
            items.append(item)
        return [{"type": "portfolio_links", "items": items}]
