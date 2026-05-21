"""Tests for PortfolioLinkSanitizer."""

from __future__ import annotations

from bookcraft.components.portfolio.link_sanitizer import PortfolioLinkSanitizer

sanitizer = PortfolioLinkSanitizer()


def _sample(title: str, url: str, service: str = "cover_design_illustration") -> dict:
    return {"title": title, "url": url, "service": service}


def test_trims_url_whitespace_and_newline() -> None:
    items = [_sample("Book A", "  https://amazon.com/book-a  ")]
    links = sanitizer.sanitize(items)
    assert len(links) == 1
    assert links[0].url == "https://amazon.com/book-a"


def test_removes_trailing_newline_artifact() -> None:
    """URL with embedded newline should have whitespace stripped."""
    items = [_sample("Book B", "https://amazon.com/book-b\n")]
    links = sanitizer.sanitize(items)
    assert len(links) == 1
    assert "\n" not in links[0].url
    assert links[0].url == "https://amazon.com/book-b"


def test_removes_trailing_dash_n_artifact_when_newline_present() -> None:
    """Trailing '-n' is a format artifact when URL had an embedded newline."""
    items = [_sample("Book C", "https://amazon.com/book-c-\ntrailing")]
    links = sanitizer.sanitize(items)
    assert len(links) == 1
    # URL after stripping newline: "https://amazon.com/book-c-trailing" → no '-n' at end
    assert "\n" not in links[0].url


def test_trailing_dash_n_artifact_from_split() -> None:
    """Simulates a URL whose newline split leaves '-n' at the end."""
    # Original: "https://amazon.com/my-book\n" → after embed-newline removal: "https://amazon.com/my-book"
    # After partial concatenation artifact: "https://amazon.com/my-book-n"
    # Our sanitizer removes '-n' only when original had a newline.
    items = [_sample("Book D", "https://amazon.com/my-book-\nn")]
    links = sanitizer.sanitize(items)
    assert len(links) == 1
    assert links[0].url == "https://amazon.com/my-book"


def test_rejects_non_https_url() -> None:
    items = [
        _sample("Book E", "http://amazon.com/book-e"),
        _sample("Book F", "ftp://files.com/book-f"),
        _sample("Book G", "not-a-url"),
    ]
    links = sanitizer.sanitize(items)
    assert len(links) == 0


def test_deduplicates_links() -> None:
    items = [
        _sample("Book H", "https://amazon.com/book-h"),
        _sample("Book H duplicate", "https://amazon.com/book-h"),
        _sample("Book I", "https://amazon.com/book-i"),
    ]
    links = sanitizer.sanitize(items)
    assert len(links) == 2
    urls = [link.url for link in links]
    assert urls.count("https://amazon.com/book-h") == 1


def test_preserves_title() -> None:
    items = [_sample("The Great Gatsby Cover", "https://amazon.com/gatsby")]
    links = sanitizer.sanitize(items)
    assert len(links) == 1
    assert links[0].title == "The Great Gatsby Cover"


def test_preserves_service() -> None:
    items = [_sample("Fantasy Cover", "https://amazon.com/fantasy", "cover_design_illustration")]
    links = sanitizer.sanitize(items)
    assert links[0].service == "cover_design_illustration"


def test_empty_list_returns_empty() -> None:
    assert sanitizer.sanitize([]) == []


def test_missing_url_skipped() -> None:
    items = [{"title": "No URL", "service": "ghostwriting"}]
    links = sanitizer.sanitize(items)
    assert len(links) == 0


def test_accepts_pydantic_like_objects() -> None:
    """Sanitizer should work with objects that have attributes, not just dicts."""

    class FakeSample:
        title = "Attribute Book"
        url = "https://amazon.com/attribute-book"
        service = "ghostwriting"
        reason_selected = "best match"

    links = sanitizer.sanitize([FakeSample()])
    assert len(links) == 1
    assert links[0].title == "Attribute Book"
    assert links[0].url == "https://amazon.com/attribute-book"
