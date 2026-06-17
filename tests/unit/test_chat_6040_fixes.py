"""Fixes from chat 6040: pasted-media-URL attachment + always-ask-phone.

(Issue 1 — LLM-only sign-off name reaching the lead/CSR — is exercised end-to-end
by scripts/e2e_consultation_lead_flow.py scenario S5.)
"""
from __future__ import annotations

from types import SimpleNamespace

from bookcraft.components.attachments.intake import (
    AttachmentIntakeProcessor,
    _attachments_from_message,
)
from bookcraft.components.sales.consultation_state import (
    ConsultationStage,
    reduce_consultation_state,
)
from bookcraft.domain.state import ThreadState


# ── Issue 2: pasted media URL becomes an attachment ─────────────────────────
class TestPastedMediaUrlAttachment:
    def test_docx_url_with_space_in_path_detected(self) -> None:
        # Verbatim shape from the transcript (note the space in "Chapter 2_…").
        msg = ("It shows uploaded. https://server.trusoft.pk/media/assets/"
               "Chapter 2_2026-06-17T13-59-55-251Z.docx Did you get it?")
        atts = _attachments_from_message(msg)
        assert len(atts) == 1
        assert atts[0].filename.endswith(".docx")
        assert atts[0].storage_key.startswith("https://server.trusoft.pk")

    def test_process_registers_url_attachment_as_manuscript(self) -> None:
        r = AttachmentIntakeProcessor().process(
            attachments=None,
            message="https://server.trusoft.pk/media/assets/Chapter 2_x.docx",
        )
        assert r.attachments, "pasted chapter URL must register an attachment"
        assert r.attachments[0].category == "manuscript"
        assert r.assessment_type is not None
        assert any("media_url_detected" in a for a in r.audit)

    def test_pdf_and_image_urls(self) -> None:
        assert _attachments_from_message("my draft https://x.io/files/draft.pdf")[0].filename == "draft.pdf"
        # Category is inferred during process(), not on the raw synthesized attachment.
        r = AttachmentIntakeProcessor().process(
            attachments=None, message="cover https://x.io/c/cover-art.png"
        )
        assert r.attachments[0].category in {"cover_design", "other"}

    def test_non_media_url_ignored(self) -> None:
        assert _attachments_from_message("see https://example.com/about for info") == []

    def test_no_url_no_attachment(self) -> None:
        r = AttachmentIntakeProcessor().process(attachments=None, message="just chatting")
        assert r.attachments == []
        assert r.audit == ["no_attachments"]


# ── Issue 3: always ask for phone once before booking ───────────────────────
def _intent():
    return SimpleNamespace(query_primary=None)


def _reduce(*, state=None, has_email=True, has_phone=False, require_phone=True, message="let's book a consultation"):
    return reduce_consultation_state(
        state=state or ThreadState(),
        message=message,
        intent=_intent(),
        contact_ready=True,
        has_email=has_email,
        has_phone=has_phone,
        require_phone=require_phone,
    )


class TestAlwaysAskPhone:
    def test_email_only_asks_for_phone(self) -> None:
        d = _reduce(has_email=True, has_phone=False)
        assert d.stage == ConsultationStage.REQUESTED_PHONE_NEEDED
        assert d.next_question == "missing_phone"
        assert d.stop_discovery is True

    def test_phone_present_does_not_ask(self) -> None:
        d = _reduce(has_email=True, has_phone=True)
        assert d.stage != ConsultationStage.REQUESTED_PHONE_NEEDED

    def test_loop_safe_after_asked_once(self) -> None:
        st = ThreadState()
        st.consultation_stage = ConsultationStage.REQUESTED_PHONE_NEEDED.value
        d = _reduce(state=st, has_email=True, has_phone=False)
        # Already asked → proceeds to the time ask, does not loop on phone.
        assert d.stage == ConsultationStage.REQUESTED_TIME_NEEDED

    def test_flag_off_disables(self) -> None:
        d = _reduce(has_email=True, has_phone=False, require_phone=False)
        assert d.stage != ConsultationStage.REQUESTED_PHONE_NEEDED

    def test_no_email_no_phone_asks_contact_first(self) -> None:
        # Genuinely no contact yet → normal contact ask, not the phone enrichment.
        d = reduce_consultation_state(
            state=ThreadState(), message="book a consultation", intent=_intent(),
            contact_ready=False, has_email=False, has_phone=False, require_phone=True,
        )
        assert d.stage == ConsultationStage.REQUESTED_CONTACT_NEEDED
