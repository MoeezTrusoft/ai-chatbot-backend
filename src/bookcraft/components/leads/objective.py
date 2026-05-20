"""Lead Objective Engine.

Decides when to stop discovery loops and move toward lead capture / consultation handoff.
Engines compute. Claude writes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.leads.contact import ContactCaptureResult
from bookcraft.domain.enums import QueryIntentType

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

LeadObjectiveStage = Literal[
    "engaging",
    "qualifying",
    "consultation_offered",
    "contact_requested",
    "contact_captured",
    "lead_ready",
    "lead_created",
    "consultation_requested",
    "consultation_pending",
    "consultation_booked",
    "blocked",
]


class LeadObjectiveDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: LeadObjectiveStage
    objective_move: Literal[
        "continue_light_discovery",
        "ask_contact",
        "create_lead",
        "offer_consultation",
        "schedule_consultation",
        "handoff_to_specialist",
        "no_change",
    ]
    reason: str
    stop_discovery: bool = False
    recommended_primary_goal: str | None = None
    next_question: str | None = None
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Trigger classification
# ---------------------------------------------------------------------------

_CONTACT_INTENT_RE = __import__("re").compile(
    r"\b(?:contact\s+me|call\s+me|email\s+me|reach\s+me|get\s+in\s+touch|"
    r"let'?s\s+(?:start|begin|proceed|go|connect)|i\s+(?:want\s+to\s+proceed|'?m\s+ready|"
    r"want\s+to\s+start)|proceed|sign\s+up|book\s+(?:now|a\s+call|a\s+consultation))\b",
    __import__("re").IGNORECASE,
)
_TIMELINE_OR_PRICE_HINT_RE = __import__("re").compile(
    r"\b(?:how\s+much|cost|price|pricing|how\s+long|timeline|turnaround|samples?|"
    r"example|portfolio|how\s+it\s+works|process)\b",
    __import__("re").IGNORECASE,
)

# Intents that should trigger lead capture (not more discovery).
_LEAD_CAPTURE_INTENTS: frozenset[QueryIntentType] = frozenset(
    {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
        QueryIntentType.CONSULTATION_REQUEST,
        QueryIntentType.NDA_REQUEST,
        QueryIntentType.AGREEMENT_REQUEST,
        QueryIntentType.PORTFOLIO_REQUEST,
        QueryIntentType.READY_TO_BUY,
        QueryIntentType.PAYMENT_QUESTION,
    }
)

_SERVICE_QUESTION_INTENTS: frozenset[QueryIntentType] = frozenset(
    {
        QueryIntentType.SERVICE_QUESTION,
        QueryIntentType.REVISION_QUESTION,
        QueryIntentType.PUBLISHING_PLATFORM_QUESTION,
    }
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class LeadObjectiveEngine:
    """Decides when to stop discovery and move toward lead capture."""

    def decide(
        self,
        *,
        message: str,
        intent: Any,  # IntentVote
        state: Any,  # ThreadState
        context_pack: Any | None = None,
        response_plan: Any | None = None,
        action_plan: Any | None = None,
        attachment_intake: Any | None = None,
        flexible_intent: Any | None = None,
        portfolio_fallback: Any | None = None,
        delegated_decision: Any | None = None,
        contact_capture: ContactCaptureResult | None = None,
    ) -> LeadObjectiveDecision:
        audit: list[str] = []

        # ── Gather signals ────────────────────────────────────────────────
        # Already created → no change needed.
        if getattr(state, "lead_created", False):
            audit.append("signal:lead_already_created")
            return LeadObjectiveDecision(
                stage="lead_created",
                objective_move="no_change",
                reason="Lead already created for this thread.",
                stop_discovery=True,
                recommended_primary_goal="lead_created_confirmation",
                audit=audit,
            )

        current_stage: str = getattr(state, "lead_objective_stage", None) or "engaging"
        if current_stage not in {
            "engaging",
            "qualifying",
            "consultation_offered",
            "contact_requested",
            "contact_captured",
            "lead_ready",
            "lead_created",
            "consultation_requested",
            "consultation_pending",
            "consultation_booked",
            "blocked",
        }:
            current_stage = "engaging"

        # Contact ready → create lead.
        if contact_capture is not None and contact_capture.lead_contact_ready:
            audit.append("signal:contact_ready")
            return LeadObjectiveDecision(
                stage="lead_ready",
                objective_move="create_lead",
                reason="Contact info (name + email or phone) is present.",
                stop_discovery=True,
                recommended_primary_goal="lead_contact_capture",
                audit=audit,
            )

        # Attachment or assessment → specialist handoff.
        has_assessment = (
            attachment_intake is not None
            and getattr(attachment_intake, "assessment_type", None) is not None
        ) or (context_pack is not None and getattr(context_pack, "assessment_type", None))
        specialist_role = (
            getattr(attachment_intake, "specialist_role", None)
            if attachment_intake
            else (getattr(context_pack, "specialist_role", None) if context_pack else None)
        )
        if has_assessment:
            audit.append(f"signal:assessment_exists:{specialist_role}")
            return LeadObjectiveDecision(
                stage="contact_requested",
                objective_move="ask_contact",
                reason="Assessment type detected; routing to specialist requires contact.",
                stop_discovery=True,
                recommended_primary_goal="lead_contact_capture",
                next_question="name_and_email_or_phone",
                audit=audit,
            )

        # Direct contact/readiness intent.
        if _CONTACT_INTENT_RE.search(message):
            audit.append("signal:contact_intent_phrase")
            return LeadObjectiveDecision(
                stage="contact_requested",
                objective_move="ask_contact",
                reason="User expressed readiness or contact intent.",
                stop_discovery=True,
                recommended_primary_goal="lead_contact_capture",
                next_question="name_and_email_or_phone",
                audit=audit,
            )

        # Pricing / timeline / consultation → stop discovery, capture contact.
        query_primary = getattr(intent, "query_primary", None)
        if query_primary in _LEAD_CAPTURE_INTENTS:
            audit.append(f"signal:lead_intent:{query_primary}")
            if query_primary == QueryIntentType.CONSULTATION_REQUEST:
                return LeadObjectiveDecision(
                    stage="consultation_offered",
                    objective_move="offer_consultation",
                    reason="User requested consultation.",
                    stop_discovery=True,
                    recommended_primary_goal="consultation_handoff",
                    next_question="name_and_email_or_phone",
                    audit=audit,
                )
            return LeadObjectiveDecision(
                stage="contact_requested",
                objective_move="ask_contact",
                reason=f"High-intent query ({query_primary}): stop discovery, capture contact.",
                stop_discovery=True,
                recommended_primary_goal="lead_contact_capture",
                next_question="name_and_email_or_phone",
                audit=audit,
            )

        if _TIMELINE_OR_PRICE_HINT_RE.search(message):
            audit.append("signal:message_level_price_timeline_process_or_samples")
            return LeadObjectiveDecision(
                stage="contact_requested",
                objective_move="ask_contact",
                reason=(
                    "Message asks pricing/timeline/samples/process; route to contact capture "
                    "instead of deep discovery."
                ),
                stop_discovery=True,
                recommended_primary_goal="lead_contact_capture",
                next_question="name_and_email_or_phone",
                audit=audit,
            )

        # Ready to buy.
        if query_primary == QueryIntentType.READY_TO_BUY:
            audit.append("signal:ready_to_buy")
            return LeadObjectiveDecision(
                stage="contact_requested",
                objective_move="ask_contact",
                reason="User is ready to buy.",
                stop_discovery=True,
                recommended_primary_goal="lead_contact_capture",
                next_question="name_and_email_or_phone",
                audit=audit,
            )

        # BookCraft discretion / delegation → consultation.
        flexible_detected = flexible_intent is not None and getattr(
            flexible_intent, "detected", False
        )
        delegated_status = (
            getattr(delegated_decision, "status", None) if delegated_decision else None
        )
        if flexible_detected or delegated_status == "delegated":
            mode = getattr(flexible_intent, "mode", None) if flexible_intent else None
            audit.append(f"signal:discretion:mode={mode}")
            return LeadObjectiveDecision(
                stage="consultation_offered",
                objective_move="offer_consultation",
                reason="User delegated decisions to BookCraft.",
                stop_discovery=True,
                recommended_primary_goal="consultation_handoff",
                next_question="name_and_email_or_phone",
                audit=audit,
            )

        # Portfolio fallback insistence → ask contact.
        if portfolio_fallback is not None:
            strategy = getattr(portfolio_fallback, "strategy", None)
            if strategy in ("fallback_general_samples", "fallback_service_samples"):
                audit.append(f"signal:portfolio_fallback:{strategy}")
                return LeadObjectiveDecision(
                    stage="contact_requested",
                    objective_move="ask_contact",
                    reason="Portfolio fallback: samples shown, lead capture opportunity.",
                    stop_discovery=True,
                    recommended_primary_goal="lead_contact_capture",
                    next_question="name_and_email_or_phone",
                    audit=audit,
                )

        # Service question with known service → move toward contact.
        service_primary = getattr(intent, "service_primary", None)
        if service_primary and query_primary in _SERVICE_QUESTION_INTENTS:
            qualifying_turns = _count_qualifying_turns(state)
            audit.append(f"signal:service_known:{service_primary},turns:{qualifying_turns}")
            if qualifying_turns >= 2:
                return LeadObjectiveDecision(
                    stage="contact_requested",
                    objective_move="ask_contact",
                    reason="Service known, enough qualifying turns — capture contact.",
                    stop_discovery=True,
                    recommended_primary_goal="lead_contact_capture",
                    next_question="name_and_email_or_phone",
                    audit=audit,
                )

        # Default: light discovery continues.
        audit.append("signal:continue_discovery")
        return LeadObjectiveDecision(
            stage=current_stage,
            objective_move="continue_light_discovery",
            reason="Not enough signals yet to move to lead capture.",
            stop_discovery=False,
            recommended_primary_goal=None,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_qualifying_turns(state: Any) -> int:
    """Estimate how many qualifying turns have happened (coarse proxy)."""
    # Use services_discussed + known project facts as a proxy.
    count = 0
    project = getattr(state, "project", None)
    if project:
        if getattr(getattr(project, "services_discussed", None), "__len__", lambda: 0)():
            count += 1
        if getattr(getattr(project, "genre", None), "value", None):
            count += 1
        if getattr(getattr(project, "manuscript_status", None), "value", None):
            count += 1
    return count
