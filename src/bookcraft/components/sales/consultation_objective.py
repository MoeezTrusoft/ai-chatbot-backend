"""ConsultationObjectiveEngine.

Wraps the LeadObjectiveEngine result and applies consultation-first logic:
 - answer current question before asking for contact
 - after contact is captured, ask for preferred call time
 - after call time is captured, create / confirm consultation handoff
 - do not loop back to word-count / genre / deadline discovery once contact is ready

Engines compute. Claude writes.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.sales.consultation_state import is_definite_call_time
from bookcraft.components.sales.current_question_priority import CurrentQuestionPriorityResult

# ---------------------------------------------------------------------------
# Call-time extraction (simple — used to capture preferred_call_time from
# the current message when the previous turn asked for it)
# ---------------------------------------------------------------------------

_CALL_TIME_RE = re.compile(
    r"\b(?:(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm))?|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm))?|"
    r"(?:this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+(?:morning|afternoon|evening|(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)))?|"
    r"(?:eastern|central|mountain|pacific|est|cst|mst|pst)(?:\s+time)?|"
    r"east|west|"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:\s+(?:morning|afternoon|evening|(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)))?|"
    r"tomorrow(?:\s+(?:morning|afternoon|evening|(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)))?|"
    r"next\s+(?:week|monday|tuesday|wednesday|thursday|friday)|"
    r"after\s+\d+(?:\s*(?:am|pm))?|"
    r"\d+\s*(?:am|pm)|"
    r"any\s+(?:day|time|morning|afternoon|evening)|"
    r"(?:this|next)\s+(?:week|weekend)|"
    r"morning|afternoon|evening|anytime|whenever)\b",
    re.IGNORECASE,
)


def _extract_preferred_call_time(text: str) -> str | None:
    m = _CALL_TIME_RE.search(text)
    return m.group(0).strip() if m else None


# Detects messages where the user is announcing manuscript/project status — these should
# be celebrated rather than immediately redirected to call-time capture.
_MANUSCRIPT_STATUS_RE = re.compile(
    r"\b(?:"
    r"(?:just\s+)?(?:finished|completed|done|wrapped\s+up|finalized)\s+"
    r"(?:the\s+)?(?:final\s+)?(?:chapter|draft|manuscript|book|novel|writing|editing|revision)|"
    r"(?:my|the)\s+(?:manuscript|book|novel|draft|chapter)\s+(?:is\s+)?(?:done|ready|complete|finished)|"
    r"(?:i've|i\s+have)\s+(?:just\s+)?(?:finished|completed)\s+(?:writing|the\s+)?(?:it|my|the)"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class ConsultationObjectiveDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    objective_move: str
    consultation_first: bool = True
    stop_discovery: bool = False
    ask_contact: bool = False
    ask_preferred_time: bool = False
    create_handoff: bool = False
    recommended_primary_goal: str | None = None
    next_question: str | None = None
    extracted_preferred_call_time: str | None = None
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ConsultationObjectiveEngine:
    """
    Consultation-first wrapper around LeadObjectiveEngine logic.

    Priority cascade (highest to lowest):
    1. Contact + call time ready → create handoff
    2. Contact ready but call time missing → ask for call time
    3. Current question has priority → answer first
    4. Lead objective says create_lead / ask_contact → honour it
    5. Continue light discovery
    """

    def decide(
        self,
        *,
        message: str,
        state: Any,  # ThreadState
        lead_objective_decision: Any,  # LeadObjectiveDecision
        contact_capture: Any | None = None,  # ContactCaptureResult
        current_question_priority: CurrentQuestionPriorityResult | None = None,
        require_phone: bool = False,
    ) -> ConsultationObjectiveDecision:
        audit: list[str] = []

        # ── Gather state signals ──────────────────────────────────────────
        contact_ready: bool = (
            contact_capture is not None and contact_capture.lead_contact_ready
        ) or bool(getattr(state, "lead_created", False))

        preferred_call_time: str | None = getattr(state, "preferred_call_time", None)
        consultation_stage: str = getattr(state, "consultation_stage", None) or "engaging"
        handoff_created: bool = bool(getattr(state, "consultation_handoff_created", False))

        lead_created: bool = bool(getattr(state, "lead_created", False))
        lod_move: str = getattr(
            lead_objective_decision, "objective_move", "continue_light_discovery"
        )
        lod_stage: str = getattr(lead_objective_decision, "stage", "engaging")

        audit.append(f"contact_ready:{contact_ready}")
        audit.append(f"lead_created:{lead_created}")
        audit.append(f"preferred_call_time_present:{preferred_call_time is not None}")
        audit.append(f"consultation_stage:{consultation_stage}")
        audit.append(f"handoff_created:{handoff_created}")

        # Whether the lead was already confirmed from a PREVIOUS turn.
        # (lead_created on state = persisted; lod_move == "create_lead" means creating *now*)
        lead_confirmed_prior_turn = lead_created and lod_move not in {"create_lead"}

        # A time only lets us book if it pins down a specific day AND clock time.
        _has_definite_time = is_definite_call_time(preferred_call_time)
        # Manuscript/project news — celebrate first, don't interrupt to schedule.
        _is_manuscript_update = bool(_MANUSCRIPT_STATUS_RE.search(message))
        audit.append(f"has_definite_time:{_has_definite_time}")

        # ── Priority 0: secure a phone before scheduling ──────────────────
        # Phone is the primary contact for a consultation CALL. When the contact is
        # email-only and a phone is required, ask for it ONCE before pivoting to the
        # call-time/handoff — even if the customer prefers email. Loop-safe via
        # state.consultation_phone_asked; never blocks (asked once, then scheduling
        # proceeds). Skipped on the create_lead turn (the lead path asks there) and when
        # celebrating a manuscript milestone.
        _has_email = bool(getattr(contact_capture, "has_email", False))
        _has_phone = bool(getattr(contact_capture, "has_phone", False))
        _phone_already_asked = bool(getattr(state, "consultation_phone_asked", False))
        if (
            require_phone
            and contact_ready
            and _has_email
            and not _has_phone
            and not _phone_already_asked
            and not handoff_created
            and lod_move not in {"create_lead"}
            and not _is_manuscript_update
        ):
            audit.append("move:ask_phone_before_scheduling")
            return ConsultationObjectiveDecision(
                stage="consultation_phone_requested",
                objective_move="ask_preferred_call_time",
                consultation_first=True,
                stop_discovery=True,
                ask_contact=True,
                recommended_primary_goal="consultation_time_capture",
                next_question="missing_phone",
                audit=audit,
            )

        # ── Priority 1: contact + DEFINITE call time → handoff ────────────
        # Skip if handoff was already created on a prior turn — don't re-trigger it.
        if (
            contact_ready
            and preferred_call_time
            and _has_definite_time
            and not handoff_created
            and lod_move not in {"create_lead"}
        ):
            audit.append("move:create_handoff")
            return ConsultationObjectiveDecision(
                stage="consultation_pending",
                objective_move="create_consultation_handoff",
                consultation_first=True,
                stop_discovery=True,
                create_handoff=True,
                recommended_primary_goal="consultation_handoff_confirmation",
                next_question=None,
                audit=audit,
            )

        # ── Priority 1.5: contact + INDEFINITE call time → offer slots ─────
        # The customer gave a vague time ("anytime", "next week", "Friday"). Don't
        # silently coerce it — offer concrete half-hour openings to pin it down.
        if (
            contact_ready
            and preferred_call_time
            and not _has_definite_time
            and not handoff_created
            and lod_move not in {"create_lead"}
            and not _is_manuscript_update
        ):
            audit.append("move:offer_time_slots")
            return ConsultationObjectiveDecision(
                stage="consultation_time_requested",
                objective_move="ask_preferred_call_time",
                consultation_first=True,
                stop_discovery=True,
                ask_preferred_time=True,
                recommended_primary_goal="consultation_time_capture",
                next_question="preferred_call_time_slots",
                audit=audit,
            )

        # ── Priority 2: contact ready (from a PRIOR turn), call time missing → ask for it ──
        # If we are creating the lead *this turn* (lod_move == "create_lead"), let the
        # lead-creation confirmation happen first; the call-time ask comes next turn.
        # Skip entirely if handoff is already created — no need to keep asking.
        # Also skip if the user is announcing manuscript/project news — celebrate first,
        # ask for call time next turn so we don't interrupt their moment.
        if _is_manuscript_update:
            audit.append("skip_priority2:manuscript_status_update")
        if contact_ready and not preferred_call_time and lead_confirmed_prior_turn and not handoff_created and not _is_manuscript_update:
            # Try to extract call time from the *current* message in case the
            # user provided it on the same turn as a follow-up.
            extracted_time = _extract_preferred_call_time(message)
            if extracted_time and is_definite_call_time(extracted_time):
                audit.append(f"call_time_extracted_this_turn:{extracted_time}")
                return ConsultationObjectiveDecision(
                    stage="consultation_pending",
                    objective_move="create_consultation_handoff",
                    consultation_first=True,
                    stop_discovery=True,
                    create_handoff=True,
                    recommended_primary_goal="consultation_handoff_confirmation",
                    next_question=None,
                    extracted_preferred_call_time=extracted_time,
                    audit=audit,
                )
            if extracted_time:
                # Vague time captured this turn ("anytime", "next week") — store it
                # but offer concrete slots instead of booking on a guess.
                audit.append(f"indefinite_call_time_extracted:{extracted_time}")
                return ConsultationObjectiveDecision(
                    stage="consultation_time_requested",
                    objective_move="ask_preferred_call_time",
                    consultation_first=True,
                    stop_discovery=True,
                    ask_preferred_time=True,
                    recommended_primary_goal="consultation_time_capture",
                    next_question="preferred_call_time_slots",
                    extracted_preferred_call_time=extracted_time,
                    audit=audit,
                )
            audit.append("move:ask_preferred_call_time")
            return ConsultationObjectiveDecision(
                stage="consultation_time_requested",
                objective_move="ask_preferred_call_time",
                consultation_first=True,
                stop_discovery=True,
                ask_preferred_time=True,
                recommended_primary_goal="consultation_time_capture",
                next_question="preferred_call_time",
                audit=audit,
            )

        # ── Priority 3: current question has priority → answer first ──────
        if current_question_priority is not None and current_question_priority.has_priority:
            qt = current_question_priority.question_type
            audit.append(f"move:answer_then_consult:{qt}")
            # Contact refusal: respect — do not push contact capture.
            if qt == "contact_refusal":
                return ConsultationObjectiveDecision(
                    stage="answering_current_question",
                    objective_move="answer_then_consultation",
                    consultation_first=True,
                    stop_discovery=False,
                    ask_contact=False,
                    recommended_primary_goal="answer_current_question",
                    next_question="consultation_interest",
                    audit=audit,
                )
            # Topic correction: suppress old path, answer corrected topic.
            if qt == "topic_correction" or current_question_priority.suppress_old_sales_path:
                return ConsultationObjectiveDecision(
                    stage="answering_current_question",
                    objective_move="answer_then_consultation",
                    consultation_first=True,
                    stop_discovery=False,
                    recommended_primary_goal="answer_current_question",
                    next_question="consultation_interest",
                    audit=audit,
                )
            # For other priority questions — answer first, bridge to consultation.
            return ConsultationObjectiveDecision(
                stage="answering_current_question",
                objective_move="answer_then_consultation",
                consultation_first=True,
                stop_discovery=False,
                recommended_primary_goal="answer_current_question",
                next_question="consultation_interest",
                audit=audit,
            )

        # ── Priority 4: honour lead objective decision ────────────────────
        if lod_move in {"create_lead", "ask_contact"}:
            audit.append(f"honouring_lead_objective:{lod_move}")
            # Do NOT set recommended_primary_goal when creating the lead — let the
            # planner's lead_created_confirmation path handle the response goal.
            # "ask_contact" can carry the goal from the lead objective decision.
            rg = (
                getattr(lead_objective_decision, "recommended_primary_goal", None)
                if lod_move == "ask_contact"
                else None  # create_lead: goal determined by planner after action dispatch
            )
            return ConsultationObjectiveDecision(
                stage=lod_stage,
                objective_move=lod_move,
                consultation_first=True,
                stop_discovery=getattr(lead_objective_decision, "stop_discovery", False),
                ask_contact=True,
                recommended_primary_goal=rg,
                next_question=getattr(
                    lead_objective_decision, "next_question", "name_and_email_or_phone"
                ),
                audit=audit,
            )

        if lod_move in {"offer_consultation", "schedule_consultation"}:
            audit.append("move:consultation_offer")
            return ConsultationObjectiveDecision(
                stage=lod_stage,
                objective_move=lod_move,
                consultation_first=True,
                stop_discovery=getattr(lead_objective_decision, "stop_discovery", False),
                ask_contact=True,
                recommended_primary_goal="consultation_offer",
                next_question="name_and_email_or_phone",
                audit=audit,
            )

        # ── Priority 5: continue discovery ───────────────────────────────
        audit.append("move:continue_conversation")
        return ConsultationObjectiveDecision(
            stage=consultation_stage,
            objective_move="continue_conversation",
            consultation_first=True,
            stop_discovery=False,
            recommended_primary_goal=None,
            next_question=None,
            audit=audit,
        )
