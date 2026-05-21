"""AttachmentAssessmentPriority — drives response planning when attachments are present.

When attachments exist, the bot must:
- acknowledge receipt
- NOT claim to have read/analysed content
- identify the assessment type and specialist
- route toward consultation/contact capture

Engines compute. Claude writes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.attachments.intake import AttachmentIntakeResult

# ---------------------------------------------------------------------------
# Slots that must never be asked before assessment handoff
# ---------------------------------------------------------------------------

ATTACHMENT_SUPPRESSED_SLOTS: list[str] = [
    "word_or_page_count",
    "word_count",
    "page_count",
    "genre",
    "draft_status",
    "manuscript_stage",
    "manuscript_status",
    "cover_style",
    "deadline",
]

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class AttachmentAssessmentPriorityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_attachment_priority: bool = False
    assessment_type: str | None = None
    specialist_role: str | None = None
    suppress_slots: list[str] = Field(default_factory=list)
    recommended_primary_goal: str | None = None
    recommended_next_question: str | None = None
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------


class AttachmentAssessmentPriority:
    """Derives assessment priority guidance from the attachment intake result.

    Used by ResponsePlanner and ContextPackBuilder to suppress scoping slots
    and route the response toward a specialist assessment handoff.
    """

    def decide(
        self,
        intake: AttachmentIntakeResult,
        *,
        contact_ready: bool = False,
    ) -> AttachmentAssessmentPriorityDecision:
        audit: list[str] = []

        if not intake.attachments:
            audit.append("no_attachments:skip")
            return AttachmentAssessmentPriorityDecision(audit=audit)

        audit.append(f"attachments:{len(intake.attachments)}")
        audit.append(f"assessment_type:{intake.assessment_type}")
        audit.append(f"specialist_role:{intake.specialist_role}")
        audit.append(f"contact_ready:{contact_ready}")

        # Primary goal: handoff to specialist assessment.
        if intake.assessment_type:
            goal = "assessment_handoff"
        else:
            goal = "attachment_received_assessment"

        # Next question: if contact not yet captured, ask for it.
        next_q = "name_and_email_or_phone" if not contact_ready else "consultation_interest"
        audit.append(f"recommended_next_question:{next_q}")

        return AttachmentAssessmentPriorityDecision(
            has_attachment_priority=True,
            assessment_type=intake.assessment_type,
            specialist_role=intake.specialist_role,
            suppress_slots=list(ATTACHMENT_SUPPRESSED_SLOTS),
            recommended_primary_goal=goal,
            recommended_next_question=next_q,
            audit=audit,
        )
