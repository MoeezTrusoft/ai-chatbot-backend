"""Tests for AttachmentAssessmentPriority."""

from __future__ import annotations

from bookcraft.components.attachments.assessment_priority import (
    ATTACHMENT_SUPPRESSED_SLOTS,
    AttachmentAssessmentPriority,
)
from bookcraft.components.attachments.intake import AttachmentIntakeResult, ChatAttachment


def _make_intake(
    filename: str = "my_draft.docx",
    assessment_type: str | None = "editorial_assessment",
    specialist_role: str | None = "senior editorial specialist",
) -> AttachmentIntakeResult:
    att = ChatAttachment(filename=filename, category="manuscript")
    return AttachmentIntakeResult(
        attachments=[att],
        detected_categories=["manuscript"],
        assessment_type=assessment_type,
        specialist_role=specialist_role,
        content_analysis_allowed=False,
    )


def _cover_intake() -> AttachmentIntakeResult:
    att = ChatAttachment(filename="cover_sketch.jpg", category="cover_design")
    return AttachmentIntakeResult(
        attachments=[att],
        detected_categories=["cover_design"],
        assessment_type="cover_design_assessment",
        specialist_role="senior cover design specialist",
        content_analysis_allowed=False,
    )


def _empty_intake() -> AttachmentIntakeResult:
    return AttachmentIntakeResult(audit=["no_attachments"])


priority = AttachmentAssessmentPriority()


def test_manuscript_attachment_prioritizes_editorial_handoff() -> None:
    decision = priority.decide(_make_intake())
    assert decision.has_attachment_priority is True
    assert decision.assessment_type == "editorial_assessment"
    assert decision.specialist_role == "senior editorial specialist"
    assert decision.recommended_primary_goal == "assessment_handoff"


def test_cover_attachment_prioritizes_cover_design_handoff() -> None:
    decision = priority.decide(_cover_intake())
    assert decision.has_attachment_priority is True
    assert decision.assessment_type == "cover_design_assessment"
    assert decision.specialist_role == "senior cover design specialist"
    assert decision.recommended_primary_goal == "assessment_handoff"


def test_attachment_priority_suppresses_word_count_and_draft_status() -> None:
    decision = priority.decide(_make_intake())
    assert "word_or_page_count" in decision.suppress_slots
    assert "word_count" in decision.suppress_slots
    assert "page_count" in decision.suppress_slots
    assert "manuscript_stage" in decision.suppress_slots
    assert "draft_status" in decision.suppress_slots
    assert "genre" in decision.suppress_slots
    assert "deadline" in decision.suppress_slots


def test_no_content_analysis_allowed() -> None:
    intake = _make_intake()
    assert intake.content_analysis_allowed is False
    priority.decide(intake)
    # Priority decision does not change the intake's content_analysis_allowed.
    assert intake.content_analysis_allowed is False


def test_no_attachment_returns_no_priority() -> None:
    decision = priority.decide(_empty_intake())
    assert decision.has_attachment_priority is False
    assert decision.suppress_slots == []


def test_contact_not_ready_asks_name_email() -> None:
    decision = priority.decide(_make_intake(), contact_ready=False)
    assert decision.recommended_next_question == "name_and_email_or_phone"


def test_contact_ready_asks_consultation_interest() -> None:
    decision = priority.decide(_make_intake(), contact_ready=True)
    assert decision.recommended_next_question == "consultation_interest"


def test_suppressed_slots_match_constant() -> None:
    decision = priority.decide(_make_intake())
    for slot in ATTACHMENT_SUPPRESSED_SLOTS:
        assert slot in decision.suppress_slots


def test_audit_populated() -> None:
    decision = priority.decide(_make_intake())
    assert decision.audit
    assert any("assessment" in a for a in decision.audit)
