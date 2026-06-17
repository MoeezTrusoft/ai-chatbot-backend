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


class TestTimezoneFromPersonalUnblocksBooking:
    """BUG-6040: the LLM stores the timezone in personal.timezone, but the reducer only
    checked preferred_timezone / consultation.customer_timezone — so a relative-window
    booking stalled forever at time_captured_needs_timezone even though the timezone
    was known. The reducer must also honour personal.timezone."""

    def _state_with_tz(self, tz: str | None):
        from bookcraft.domain.enums import Source
        from bookcraft.domain.meta import FieldMeta

        st = ThreadState()
        st.preferred_call_time = "Tuesday afternoon"  # relative window → needs a timezone
        if tz:
            st.personal.timezone = FieldMeta[str](value=tz, confidence=0.92, source=Source.USER_STATED)
        return st

    def test_personal_timezone_reaches_ready_to_schedule(self) -> None:
        d = reduce_consultation_state(
            state=self._state_with_tz("America/New_York"), message="book a consultation",
            intent=_intent(), contact_ready=True, has_email=True, has_phone=True, require_phone=True,
        )
        assert d.stage == ConsultationStage.READY_TO_SCHEDULE
        assert d.can_schedule is True

    def test_no_timezone_anywhere_still_asks(self) -> None:
        d = reduce_consultation_state(
            state=self._state_with_tz(None), message="book a consultation",
            intent=_intent(), contact_ready=True, has_email=True, has_phone=True, require_phone=True,
        )
        assert d.stage == ConsultationStage.TIME_CAPTURED_NEEDS_TIMEZONE


# ── Booking executes: the reconcile path must carry a consumable pending key ──
class TestReconcilePlanCarriesConfirmationKey:
    """BUG-6040: the live flow proposes the booking via _reconcile_consultation_action_plan.
    If that plan lacks confirmation_required / pending_confirmation_key, the stage shows
    pending_confirmation but sales_actions.pending_confirmation.type stays None, so the
    customer's 'yes' is never consumed and the consultation never books."""

    def test_reconcile_sets_confirmation_key_and_expiry(self) -> None:
        from types import SimpleNamespace

        from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType
        from bookcraft.components.sales.consultation_state import ConsultationStateDecision
        from bookcraft.services.chat import _reconcile_consultation_action_plan

        cc = SimpleNamespace(
            contact=SimpleNamespace(name="Maya Author", email="maya@example.com", phone=None)
        )
        decision = ConsultationStateDecision(
            stage=ConsultationStage.READY_TO_SCHEDULE,
            can_schedule=True,
            preferred_call_time="next Tuesday at 2 PM",
        )
        plan = _reconcile_consultation_action_plan(
            current_plan=ActionPlan(status=ActionStatus.NOT_NEEDED, reason="none"),
            consultation_decision=decision,
            state=ThreadState(),
            contact_capture=cc,
        )
        assert plan.action_type == ActionType.SCHEDULE_CONSULTATION
        assert plan.status == ActionStatus.NEEDS_CONFIRMATION
        # The bits that make the pending confirmation consumable next turn:
        assert plan.confirmation_required is True
        assert plan.pending_confirmation_key == ActionType.SCHEDULE_CONSULTATION.value
        assert plan.pending_expires_at is not None
