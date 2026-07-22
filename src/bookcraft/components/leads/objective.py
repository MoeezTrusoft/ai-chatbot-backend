"""Lead Objective Engine.

Decides when to stop discovery loops and move toward lead capture / consultation handoff.
Engines compute. Claude writes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.leads.contact import ContactCaptureResult
from bookcraft.domain.enums import QueryIntentType

# Detects that the user's message is itself a direct question the bot should answer.
_USER_QUESTION_RE = re.compile(
    r"\b(?:how|what|which|where|when|why|can\s+you|could\s+you|do\s+you|"
    r"tell\s+me|explain|help\s+me|show\s+me|what'?s|how'?s|"
    r"what\s+(?:are|is|do|does)|how\s+(?:do|does|can|much))\b",
    re.IGNORECASE,
)

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
        "answer_then_consultation",
        "request_second_contact_method",
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
# Step 1: narrowed to genuine buying-intent phrases; informational terms removed.
# "process," "samples," "example," "portfolio," "how it works" are informational
# and should get answers before any contact ask.
_BUYING_INTENT_RE = __import__("re").compile(
    r"\b(?:how\s+much|cost|price|pricing|how\s+long|timeline|turnaround|"
    r"i\s+(?:want|need|would\s+like)\s+(?:to\s+(?:start|hire|proceed|begin)|"
    r"a\s+(?:quote|estimate|proposal))|"
    r"send\s+(?:me|us)\s+(?:a\s+)?(?:quote|proposal)|"
    r"get\s+(?:a\s+)?quote|request\s+(?:a\s+)?(?:quote|estimate)|"
    r"hire\s+you|work\s+with\s+you|sign\s+up|ready\s+to\s+(?:start|begin|proceed))\b",
    __import__("re").IGNORECASE,
)

# Step 2: explicit buying signals required for lead creation when contact-ready.
_EXPLICIT_LEAD_INTENT_RE = __import__("re").compile(
    r"\b(?:quote|estimate|proposal|consultation|schedule|book\s+a\s+call|"
    r"hire|start\s+(?:the\s+)?(?:project|work)|ready\s+to\s+(?:start|begin|proceed|buy)|"
    r"contact\s+me|call\s+me|email\s+me|reach\s+me|"
    r"i\s+want\s+to\s+(?:proceed|start|hire|work)|"
    r"please\s+(?:contact|call|reach)|"
    r"need\s+(?:a\s+)?(?:quote|estimate|ghostwriter|editor|designer))\b",
    __import__("re").IGNORECASE,
)

# Signals that indicate a complaint or non-lead context even with contact info.
_NON_LEAD_CONTEXT_RE = __import__("re").compile(
    r"\b(?:bug|broken|not\s+working|error|problem\s+with|issue\s+with|"
    r"my\s+email\s+is\s+(?:broken|not\s+working|wrong)|"
    r"privacy|complaint|annoyed|frustrated|angry|"
    r"test(?:ing)?\s+(?:your|the)\s+(?:form|system|email)|"
    r"does\s+this\s+(?:work|go\s+through)|checking\s+if|just\s+testing)\b",
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
        turn_count: int = 0,
    ) -> LeadObjectiveDecision:
        audit: list[str] = []

        # ── Gather signals ────────────────────────────────────────────────
        # Step 4: lead_created loop guard — after acknowledgment, resume normal help.
        if getattr(state, "lead_created", False):
            audit.append("signal:lead_already_created")
            # Contact enrichment: if lead is created but only has one contact method,
            # ask once for the missing second method (email or phone).
            contact_capture_arg = contact_capture
            _contact_complete = (
                contact_capture_arg is not None and contact_capture_arg.contact_complete
            )
            _second_already_requested = getattr(state, "contact_second_method_requested", False)
            # A field the customer said is unavailable ("no phone", "email only" —
            # chat 6759) must not be solicited even once: drop unavailable fields
            # from the "missing second method" ask.
            _status = getattr(state, "contact_status", {}) or {}
            _missing = [
                f
                for f in (
                    contact_capture_arg.missing_contact_fields
                    if contact_capture_arg is not None
                    else []
                )
                if _status.get(f) != "unavailable"
            ]
            if not _contact_complete and not _second_already_requested and _missing:
                audit.append("signal:contact_enrichment_opportunity")
                _next_q = "missing_phone" if "phone" in _missing else "missing_email"
                return LeadObjectiveDecision(
                    stage="lead_created",
                    objective_move="request_second_contact_method",
                    reason=(
                        "Lead created; politely asking once for the missing "
                        "second contact method."
                    ),
                    stop_discovery=True,
                    recommended_primary_goal="lead_contact_capture",
                    next_question=_next_q,
                    audit=audit,
                )
            # If the user is asking a new question (not lead-related), let them.
            # lead_created_acknowledged prevents perpetual "lead_created_confirmation" loops.
            lead_acknowledged = getattr(state, "lead_created_acknowledged", False)
            query_primary_val = getattr(intent, "query_primary", None)
            is_new_service_question = query_primary_val in {
                QueryIntentType.SERVICE_QUESTION,
                QueryIntentType.PRICING_QUESTION,
                QueryIntentType.TIMELINE_QUESTION,
                QueryIntentType.PORTFOLIO_REQUEST,
                QueryIntentType.PUBLISHING_PLATFORM_QUESTION,
                QueryIntentType.REVISION_QUESTION,
            }
            if lead_acknowledged and is_new_service_question:
                audit.append("signal:lead_created_acknowledged_resuming_discovery")
                return LeadObjectiveDecision(
                    stage="lead_created",
                    objective_move="continue_light_discovery",
                    reason="Lead already created and acknowledged; answering new question.",
                    stop_discovery=False,
                    recommended_primary_goal=None,
                    audit=audit,
                )
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

        contact_ready = contact_capture is not None and contact_capture.lead_contact_ready

        # Contact ready → create lead ONLY with explicit lead intent (Step 2).
        if contact_capture is not None and contact_capture.lead_contact_ready:
            audit.append("signal:contact_ready")
            # Do not create lead if this looks like a non-lead context (bug report, test, etc.).
            if _NON_LEAD_CONTEXT_RE.search(message):
                audit.append("signal:non_lead_context_suppresses_lead_creation")
                return LeadObjectiveDecision(
                    stage=current_stage,
                    objective_move="continue_light_discovery",
                    reason="Contact present but message context is non-lead (complaint/test/bug).",
                    stop_discovery=False,
                    recommended_primary_goal=None,
                    audit=audit,
                )
            query_primary = getattr(intent, "query_primary", None)
            # A customer who volunteers name + email/phone (CONTACT_INFO_PROVIDED) is
            # giving the strongest possible lead signal — typically right after we
            # asked for contact. Treating that as "no explicit intent" and continuing
            # discovery silently drops the lead (regression from the "." commit d3ff6e9).
            # Non-lead contexts (complaint/test/bug) are already suppressed by the
            # _NON_LEAD_CONTEXT_RE guard above, so this cannot create junk leads.
            # Scoped to this contact-ready branch only — do NOT add to
            # _LEAD_CAPTURE_INTENTS (that set also drives ask_contact behaviour).
            has_explicit_intent = (
                query_primary in _LEAD_CAPTURE_INTENTS
                or query_primary == QueryIntentType.CONTACT_INFO_PROVIDED
                or bool(_EXPLICIT_LEAD_INTENT_RE.search(message))
            )
            if has_explicit_intent:
                # Phone is the primary contact method. When the customer offered only
                # an email (even if they prefer email), still solicit a phone on the
                # very turn we create the lead — never block the lead, just ask.
                _needs_phone = contact_capture is not None and not contact_capture.has_phone
                if _needs_phone:
                    audit.append("signal:create_lead_phone_missing_will_ask")
                return LeadObjectiveDecision(
                    stage="lead_ready",
                    objective_move="create_lead",
                    reason="Contact info present and explicit lead/buying intent confirmed.",
                    stop_discovery=True,
                    recommended_primary_goal="lead_contact_capture",
                    next_question="missing_phone" if _needs_phone else None,
                    audit=audit,
                )
            # Contact ready but no explicit intent → ask what they need.
            audit.append("signal:contact_ready_no_explicit_intent")
            return LeadObjectiveDecision(
                stage="contact_captured",
                objective_move="continue_light_discovery",
                reason="Contact info present but no explicit buying/lead intent yet.",
                stop_discovery=False,
                recommended_primary_goal=None,
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

        # ── Step 3a: Welcome and engage on the first/second turn ─────────────
        # Never fire a contact ask on the very first informational message — welcome first.
        # Bypass conditions (already handled above, or high-intent signals):
        # - contact already ready → handled above
        # - attachment assessment → handled above
        # - explicit contact/readiness phrase → handled above
        # - high buying-intent intent (PRICING, CONSULTATION, READY_TO_BUY, etc.) → let through
        # - flexible delegation → let through (checked before this block)
        _query_for_first_turn = getattr(intent, "query_primary", None)
        _is_high_intent_first_turn = (
            _query_for_first_turn in _LEAD_CAPTURE_INTENTS
            or _query_for_first_turn == QueryIntentType.READY_TO_BUY
            or bool(_BUYING_INTENT_RE.search(message))
        )
        _flexible_detected_first_turn = flexible_intent is not None and getattr(
            flexible_intent, "detected", False
        )
        if (
            turn_count <= 1
            and not contact_ready
            and not _is_high_intent_first_turn
            and not _flexible_detected_first_turn
        ):
            audit.append(f"signal:first_turn_engage:turn={turn_count}")
            return LeadObjectiveDecision(
                stage="engaging",
                objective_move="continue_light_discovery",
                reason="First turn: welcome and engage before any contact ask.",
                stop_discovery=False,
                recommended_primary_goal="greeting_welcome",
                audit=audit + ["signal:first_turn_engage_before_capture"],
            )

        # ── Step 3b: Answer the user's direct question before any contact ask ─
        # When the user has asked a direct question (how/what/tell me) with a
        # service-question or general intent, answer it before capturing contact.
        # Bypass: flexible_intent detected (delegation takes priority over answer-first).
        query_primary_early = getattr(intent, "query_primary", None)
        _flexible_bypass = flexible_intent is not None and getattr(
            flexible_intent, "detected", False
        )
        if (
            not contact_ready
            and not _flexible_bypass
            and _USER_QUESTION_RE.search(message)
            and query_primary_early in _SERVICE_QUESTION_INTENTS
        ):
            audit.append(f"signal:user_question_before_capture:intent={query_primary_early}")
            return LeadObjectiveDecision(
                stage=current_stage,
                objective_move="answer_then_consultation",
                reason="User asked a direct question; answer before any contact ask.",
                stop_discovery=False,
                recommended_primary_goal="answer_current_question",
                audit=audit + ["signal:answer_question_before_contact"],
            )

        # ── Step 3c: Back off after a deflected contact ask ──────────────────
        # If the bot asked for contact last turn and the user didn't provide it,
        # back off for one turn to add value instead of demanding again.
        last_turn_asked = getattr(state, "last_turn_asked_contact", False)
        if last_turn_asked and not contact_ready:
            audit.append("signal:contact_ask_backoff:not_provided")
            return LeadObjectiveDecision(
                stage=current_stage,
                objective_move="continue_light_discovery",
                reason="Contact was just asked and not provided; back off and add value.",
                stop_discovery=False,
                recommended_primary_goal="answer_current_question",
                audit=audit + ["signal:contact_ask_backoff"],
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

        # Step 1: only trigger on genuine buying intent, not informational questions.
        if _BUYING_INTENT_RE.search(message):
            audit.append("signal:message_level_buying_intent")
            return LeadObjectiveDecision(
                stage="contact_requested",
                objective_move="ask_contact",
                reason="Message has genuine buying/pricing intent; route to contact capture.",
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
