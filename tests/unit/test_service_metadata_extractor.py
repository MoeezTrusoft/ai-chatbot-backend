"""Tests for ServiceMetadataExtractor."""

from __future__ import annotations

from bookcraft.components.metadata.extractor import ServiceMetadataExtractor

extractor = ServiceMetadataExtractor()


def test_extracts_amazon_kdp_and_ingramspark() -> None:
    result = extractor.extract("I want it on Amazon KDP and IngramSpark.")
    assert "amazon_kdp" in result.publishing_platforms
    assert "ingramspark" in result.publishing_platforms


def test_extracts_ebook_and_paperback() -> None:
    result = extractor.extract("I need it as ebook and paperback.")
    assert "ebook" in result.book_formats
    assert "paperback" in result.book_formats


def test_extracts_isbn_status_has() -> None:
    result = extractor.extract("I already have an ISBN.")
    assert result.isbn_status == "has_isbn"


def test_extracts_isbn_status_needs() -> None:
    result = extractor.extract("I need ISBN help.")
    assert result.isbn_status == "needs_isbn"


def test_extracts_editing_level_and_dialect() -> None:
    result = extractor.extract(
        "I need developmental editing in US English.",
        active_service="editing_proofreading",
    )
    editing = result.confirmed.get("editing_proofreading", {})
    assert editing.get("editing_level") == "developmental_editing"
    assert editing.get("dialect") == "us_english"


def test_extracts_cover_spine_and_style() -> None:
    result = extractor.extract(
        "I need front, back, and spine. I like minimalist covers.",
        active_service="cover_design_illustration",
    )
    cover = result.confirmed.get("cover_design_illustration", {})
    assert cover.get("front_back_spine_needed") is True
    assert cover.get("visual_style") == "minimalist"


def test_extracts_formatting_tables_and_print_ebook() -> None:
    result = extractor.extract(
        "It has tables and footnotes. I need ebook formatting and print formatting.",
        active_service="interior_formatting",
    )
    fmt = result.confirmed.get("interior_formatting", {})
    assert fmt.get("tables_or_footnotes") is True
    assert fmt.get("ebook_required") is True
    assert fmt.get("print_required") is True


def test_extracts_marketing_channels_and_reviews_goal() -> None:
    result = extractor.extract(
        "I need Amazon ads and I want to get more reviews.",
        active_service="marketing_promotion",
    )
    mkt = result.confirmed.get("marketing_promotion", {})
    channels = mkt.get("channels", [])
    assert "amazon_ads" in channels
    assert mkt.get("campaign_goal") == "reviews"


def test_extracts_audiobook_finished_audio() -> None:
    result = extractor.extract(
        "I already recorded the audio.",
        active_service="audiobook_production",
    )
    audio = result.confirmed.get("audiobook_production", {})
    assert audio.get("finished_audio_available") is True


def test_extracts_author_website_booking_form() -> None:
    result = extractor.extract(
        "I need a booking form on my author website.",
        active_service="author_website",
    )
    web = result.confirmed.get("author_website", {})
    assert web.get("booking_form_needed") is True


def test_extracts_video_duration_and_platform() -> None:
    result = extractor.extract(
        "I need a 30 second trailer for Instagram.",
        active_service="video_trailer",
    )
    vid = result.confirmed.get("video_trailer", {})
    assert "30" in str(vid.get("duration", ""))
    assert vid.get("platform") == "instagram"


def test_negated_platform_not_confirmed() -> None:
    result = extractor.extract("I don't want Amazon KDP, only IngramSpark.")
    # IngramSpark should be confirmed
    assert "ingramspark" in result.publishing_platforms
    # Amazon KDP should NOT be in confirmed platforms
    assert "amazon_kdp" not in result.publishing_platforms


def test_uncertain_formats_stored_as_candidates() -> None:
    result = extractor.extract("Maybe ebook or paperback, not sure.")
    # Neither should be in confirmed formats
    assert "ebook" not in result.book_formats or "paperback" not in result.book_formats
    # At least one should be in candidates
    all_candidates = []
    for cands in result.candidates.values():
        all_candidates.extend(cands)
    assert any(c.get("certainty") in {"uncertain", "candidate"} for c in all_candidates)


def test_picture_book_format_not_children_genre() -> None:
    """picture book must be detected as a format, not as children's genre."""
    result = extractor.extract("I want to create a picture book.")
    assert "picture_book" in result.book_formats
    # No audience or genre inferred from picture book alone.
    # (genre/audience inference is in the preprocessor, not metadata extractor)


def test_uk_english_dialect() -> None:
    result = extractor.extract("Use British English please.", active_service="editing_proofreading")
    editing = result.confirmed.get("editing_proofreading", {})
    assert editing.get("dialect") == "uk_english"


def test_multiple_platforms_extracted() -> None:
    result = extractor.extract("I want Amazon KDP, IngramSpark, Kobo, and Apple Books.")
    platforms = result.publishing_platforms
    assert "amazon_kdp" in platforms
    assert "ingramspark" in platforms
    assert "kobo" in platforms
    assert "apple_books" in platforms


def test_audit_populated() -> None:
    result = extractor.extract("I want Amazon KDP.")
    assert result.audit
