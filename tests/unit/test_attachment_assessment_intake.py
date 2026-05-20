"""Tests for the AttachmentIntakeProcessor."""

from __future__ import annotations

from bookcraft.components.attachments.intake import (
    AttachmentIntakeProcessor,
    ChatAttachment,
)

_proc = AttachmentIntakeProcessor()


def _att(filename: str, category: str | None = None) -> ChatAttachment:
    return ChatAttachment(filename=filename, category=category)  # type: ignore[arg-type]


def test_manuscript_attachment_routes_to_manuscript_assessment() -> None:
    result = _proc.process(
        attachments=[_att("my_manuscript.docx")],
        message="Here is my manuscript.",
    )
    assert result.assessment_type == "manuscript_assessment"
    assert result.specialist_role is not None
    assert "manuscript" in result.specialist_role.lower()


def test_editing_with_manuscript_routes_to_editorial_assessment() -> None:
    result = _proc.process(
        attachments=[_att("draft_chapters.docx")],
        message="I need editing.",
        active_service="editing_proofreading",
    )
    assert result.assessment_type == "editorial_assessment"
    assert "editorial" in (result.specialist_role or "").lower()


def test_cover_attachment_routes_to_cover_design_assessment() -> None:
    result = _proc.process(
        attachments=[_att("cover_design_idea.jpg")],
        message="Here is my cover idea.",
        active_service="cover_design_illustration",
    )
    assert result.assessment_type == "cover_design_assessment"
    assert "cover design" in (result.specialist_role or "").lower()


def test_voice_audio_routes_to_audiobook_or_manuscript_development() -> None:
    result = _proc.process(
        attachments=[_att("story_voice_memo.mp3")],
        message="I have a voice recording.",
        active_service="ghostwriting",
    )
    assert result.assessment_type in ("audiobook_assessment", "manuscript_development_assessment")
    assert result.specialist_role is not None


def test_outline_with_ghostwriting_routes_to_manuscript_development() -> None:
    result = _proc.process(
        attachments=[_att("book_outline.docx")],
        message="Here is my outline.",
        active_service="ghostwriting",
        manuscript_status="outline",
    )
    assert result.assessment_type == "manuscript_development_assessment"
    assert "manuscript" in (result.specialist_role or "").lower()


def test_content_analysis_is_always_false() -> None:
    result = _proc.process(
        attachments=[_att("anything.pdf")],
        message="Please review this.",
    )
    assert result.content_analysis_allowed is False


def test_specialist_role_is_set() -> None:
    result = _proc.process(
        attachments=[_att("chapter_one.docx")],
        message="Here is my first chapter.",
    )
    assert result.specialist_role is not None
    assert len(result.specialist_role) > 3


def test_filename_category_inference() -> None:
    cases = [
        ("my_manuscript.docx", "manuscript"),
        ("cover_design_v2.png", "cover_design"),
        ("project_brief.pdf", "brief"),
        ("moodboard_sample.jpg", "sample_reference"),
        ("book_outline.txt", "outline"),
        ("journal_notes.txt", "notes"),
        ("voice_memo_01.mp3", "audio"),
        ("random_file.xlsx", "other"),
    ]
    for filename, expected_cat in cases:
        result = _proc.process(
            attachments=[_att(filename)],
            message="Here is my file.",
        )
        assert len(result.attachments) == 1
        assert result.attachments[0].category == expected_cat, (
            f"filename={filename}: expected {expected_cat}, got {result.attachments[0].category}"
        )


def test_no_attachments_returns_empty_result() -> None:
    result = _proc.process(attachments=None, message="Hello.")
    assert result.attachments == []
    assert result.assessment_type is None
    assert result.content_analysis_allowed is False


def test_detected_categories_populated() -> None:
    result = _proc.process(
        attachments=[
            _att("chapter_draft.docx"),
            _att("cover_sketch.png"),
        ],
        message="Here are my files.",
    )
    assert "manuscript" in result.detected_categories
    assert "cover_design" in result.detected_categories
