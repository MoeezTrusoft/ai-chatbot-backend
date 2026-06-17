"""Tests for landing-page → service inference used to anchor a thread's active
service at greet time (so an ambiguous first message on, e.g., the cover-design
page is not mis-classified as ghostwriting)."""

from __future__ import annotations

import pytest

from bookcraft.domain.enums import ServiceCategory
from bookcraft.services.chat import _service_from_landing


@pytest.mark.parametrize(
    ("page", "keyword", "expected"),
    [
        ("/book-cover-design/", "hire a book cover designer", ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        ("https://bookcraft.com/book-cover-design/", None, ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        ("/ghostwriting-services/", None, ServiceCategory.GHOSTWRITING),
        ("/editing-proofreading/", None, ServiceCategory.EDITING_PROOFREADING),
        ("/audiobook-production/", None, ServiceCategory.AUDIOBOOK_PRODUCTION),
        ("/interior-formatting/", None, ServiceCategory.INTERIOR_FORMATTING),
        ("/", "book cover illustration", ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        (None, "ghostwriter for my novel", ServiceCategory.GHOSTWRITING),
    ],
)
def test_landing_maps_to_service(page, keyword, expected) -> None:
    assert _service_from_landing(page, keyword) == expected


@pytest.mark.parametrize(
    ("page", "keyword"),
    [
        (None, None),
        ("/", None),
        ("/about-us/", "contact"),
        ("/blog/some-author-story/", "author interview"),
    ],
)
def test_landing_without_service_signal_returns_none(page, keyword) -> None:
    assert _service_from_landing(page, keyword) is None
