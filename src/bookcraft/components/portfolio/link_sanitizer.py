"""PortfolioLinkSanitizer — cleans portfolio sample URLs before they reach the customer.

Rules:
- strip surrounding whitespace
- remove embedded newlines (\\n, \\r)
- remove trailing '-n' artifact only when a newline was present (format corruption)
- validate https:// scheme
- reject malformed / non-https URLs
- deduplicate by URL
- preserve title and service metadata
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

# Matches a trailing "-n" that is a newline-splitting artifact.
# Only applied after we confirm the raw URL contained a newline character.
_TRAILING_DASH_N_RE = re.compile(r"-n$")

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class PortfolioLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    service: str | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------


def _clean_url(raw: str) -> str | None:
    """Return a sanitised https URL, or None if invalid."""
    had_newline = "\n" in raw or "\r" in raw

    # Remove embedded whitespace / newlines.
    url = raw.strip()
    url = url.replace("\r\n", "").replace("\n", "").replace("\r", "")

    # Remove trailing "-n" only when the raw URL had an embedded newline
    # (the "-" was a line-continuation artifact, "n" is the start of the next line).
    if had_newline and _TRAILING_DASH_N_RE.search(url):
        url = url[:-2]

    if not url:
        return None

    # Only https links are permitted in customer-facing output.
    if not url.startswith("https://"):
        return None

    return url


def _extract_str(item: Any, *keys: str) -> str:
    """Extract a string value from an object or dict by trying keys in order."""
    for key in keys:
        if isinstance(item, dict):
            val = item.get(key)
        else:
            val = getattr(item, key, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class PortfolioLinkSanitizer:
    """Sanitise a list of portfolio samples into clean PortfolioLink objects."""

    def sanitize(self, items: list[Any]) -> list[PortfolioLink]:
        links: list[PortfolioLink] = []
        seen_urls: set[str] = set()

        for item in items:
            raw_url = _extract_str(item, "url")
            if not raw_url:
                continue

            clean = _clean_url(raw_url)
            if not clean:
                continue

            # Deduplicate.
            if clean in seen_urls:
                continue
            seen_urls.add(clean)

            title = _extract_str(item, "title") or "BookCraft Sample"
            service_raw = _extract_str(item, "service")
            service = str(service_raw) if service_raw else None
            description = _extract_str(item, "reason_selected", "description") or None

            links.append(
                PortfolioLink(
                    title=title,
                    url=clean,
                    service=service,
                    description=description,
                )
            )

        return links
