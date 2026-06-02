from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.leads import ContactCaptureResult, LeadObjectiveDecision
from bookcraft.components.tools.governance import ToolGovernanceDecision
from bookcraft.domain.state import ThreadState

# Type alias to avoid circular import at runtime; resolved via Any in signatures.
_ConsultationObjectiveDecision = Any
_CurrentQuestionPriorityResult = Any
_AnswerBeforeCaptureDecision = Any

# Internal implementation terms that must never reach customer-facing output.
_INTERNAL_TERMS: list[str] = [
    "backend",
    "classifier",
    "runtime atoms",
    "provider votes",
    "RAG",
    "tool_governance",
    "action_plan",
    "deterministic engine",
    "quote engine",
]

_LEAD_DISCOVERY_SUPPRESSIONS: list[str] = [
    "word_or_page_count",
    "genre",
    "manuscript_stage",
    "deadline",
    "cover_style",
]

# next_question priority by planning scenario.
_COVER_DESIGN_PRIORITY: list[str] = [
    "cover_style",
    "word_or_page_count",
    "genre",
    "manuscript_stage",
    "deadline",
]

_PRICING_PRIORITY: list[str] = [
    "word_or_page_count",
    "genre",
    "manuscript_stage",
    "deadline",
]

_DEFAULT_PRIORITY: list[str] = [
    "word_or_page_count",
    "genre",
    "manuscript_stage",
    "deadline",
]

_GOAL_BY_QUERY: dict[str, str] = {
    # Core happy-path intents (original 7)
    "greeting": "greeting_welcome",
    "pricing_question": "pricing_scoping",
    "timeline_question": "pricing_scoping",
    "consultation_request": "consultation_scoping",
    "nda_request": "document_scoping",
    "agreement_request": "document_scoping",
    "portfolio_request": "portfolio_matching",
    # Gap 1: long-tail intents mapped to real goals (mission alignment audit)
    "service_question": "answer_current_question",
    "publishing_platform_question": "answer_current_question",
    "revision_question": "revision_response",
    "payment_question": "payment_guidance",
    "manuscript_status_update": "celebrate_and_advance",
    "complaint_or_objection": "complaint_recovery",
    "unclear": "gentle_clarify",
    "spam_or_abuse": "minimal_acknowledge",
    "off_topic": "friendly_redirect",
    "ready_to_buy": "lead_contact_capture",
    "contact_info_provided": "lead_contact_capture",
}

# Goals added by PR 2 (consultation-first planner).
_CONSULTATION_GOALS: frozenset[str] = frozenset(
    {
        "answer_current_question",
        "consultation_offer",
        "contact_capture_for_consultation",
        "consultation_time_capture",
        "consultation_handoff_confirmation",
    }
)

# Phrases that must never appear in responses when attachments are present.
_ATTACHMENT_FORBIDDEN_PHRASES: list[str] = [
    "i reviewed",
    "i analyzed",
    "your manuscript says",
    "your file contains",
    "i found in the attachment",
    "i read",
    "after reading",
    "having reviewed",
]

# next_question key for project-scope clarification.
_PROJECT_CLARIFICATION_QUESTION = "same_or_new_project"


class ResponsePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_facts: list[str] = Field(default_factory=list)
    primary_goal: str = "continue_discovery"
    next_question: str | None = None
    must_not_mention: list[str] = Field(default_factory=list)
    max_questions: int = 1
    tone: str = "warm_consultative"
    customer_safe_tool_summary: str | None = None
    audit: list[str] = Field(default_factory=list)


class ResponsePlanner:
    def plan(
        self,
        *,
        intent: IntentVote,
        state: ThreadState,
        context_pack: ContextPack,
        tool_governance: ToolGovernanceDecision | None = None,
        action_plan: Any | None = None,
        action_result: Any | None = None,
        negation_targets: list[Any] | None = None,
        portfolio_fallback_decision: Any | None = None,
        flexible_intent_decision: Any | None = None,
        lead_objective_decision: LeadObjectiveDecision | None = None,
        contact_capture_result: ContactCaptureResult | None = None,
        # PR 2: consultation-first planner decisions.
        consultation_objective_decision: Any | None = None,
        current_question_priority: Any | None = None,
        answer_before_capture_decision: Any | None = None,
        # PR 3: attachment assessment priority.
        attachment_priority_decision: Any | None = None,
        # Context enforcement (PR: context-enforcement).
        context_enforcement: Any | None = None,
        # Batch 4: complaint classifier output.
        complaint_classification: Any | None = None,
    ) -> ResponsePlan:
        del state, action_plan  # all project state surfaces via context_pack

        facts = _acknowledge_facts(context_pack)
        forbidden = _must_not_mention(
            context_pack,
            negation_targets=negation_targets,
            portfolio_fallback_decision=portfolio_fallback_decision,
        )
        goal = _primary_goal(
            intent,
            context_pack,
            tool_governance,
            portfolio_fallback_decision,
            flexible_intent_decision,
            lead_objective_decision,
            consultation_objective_decision=consultation_objective_decision,
            current_question_priority=current_question_priority,
        )
        nq = _next_question(
            intent,
            context_pack,
            goal,
            flexible_intent_decision,
            lead_objective_decision,
            contact_capture_result,
            consultation_objective_decision=consultation_objective_decision,
        )
        summary = _customer_safe_tool_summary(tool_governance, action_result)
        max_questions = 1
        if lead_objective_decision is not None and lead_objective_decision.stop_discovery:
            max_questions = 1
            for item in _LEAD_DISCOVERY_SUPPRESSIONS:
                if item not in forbidden:
                    forbidden.append(item)

        # PR 3: suppress scoping slots from forbidden when attachment priority active.
        if attachment_priority_decision is not None and getattr(
            attachment_priority_decision, "has_attachment_priority", False
        ):
            for slot in getattr(attachment_priority_decision, "suppress_slots", []):
                if slot not in forbidden:
                    forbidden.append(str(slot))

        # Context enforcement overrides (highest priority after governance).
        if context_enforcement is not None:
            _enf_goal = getattr(context_enforcement, "forced_primary_goal", None)
            _enf_nq = getattr(context_enforcement, "forced_next_question", None)
            _enf_forbidden = list(getattr(context_enforcement, "forbidden_reasks", None) or [])
            _enf_stale = list(getattr(context_enforcement, "stale_context_terms", None) or [])
            # Override goal only if governance allows normal operation.
            if _enf_goal and goal not in {
                "safe_blocked_action",
                "clarify_intent",
                "lead_created_confirmation",
            }:
                goal = str(_enf_goal)
            # Override next question — None is a valid "ask nothing" signal.
            if _enf_nq is not None or _enf_goal in {
                "consultation_handoff_confirmation",
                "correction_recovery",
            }:
                if _enf_nq is not None:
                    nq = str(_enf_nq)
                elif _enf_goal == "consultation_handoff_confirmation":
                    nq = None
            for _ef in _enf_forbidden:
                if _ef not in forbidden:
                    forbidden.append(_ef)
            for _st in _enf_stale:
                if _st not in forbidden:
                    forbidden.append(_st)

        # Batch 4: complaint classifier override (second-highest priority after safety/governance).
        # Only override goal for HIGH-severity complaints (privacy, abusive) to avoid
        # disrupting normal conversation flow from low-confidence pattern matches.
        # MEDIUM severity: suppress forbidden questions only; leave goal intact.
        # LOW severity: no override (context_enforcement handles wrong-service signals).
        if complaint_classification is not None and getattr(
            complaint_classification, "detected", False
        ):
            _complaint_severity = getattr(complaint_classification, "severity", "low")
            _complaint_goal = getattr(complaint_classification, "recovery_goal", None)
            _stop_sales = getattr(complaint_classification, "should_stop_sales_script", False)
            _complaint_forbidden = list(
                getattr(complaint_classification, "forbidden_questions", []) or []
            )
            if (
                _complaint_severity == "high"
                and _complaint_goal
                and goal not in {"safe_blocked_action", "clarify_intent"}
            ):
                goal = str(_complaint_goal)
                nq = None  # high-severity complaint turns must not ask a discovery question
            if _stop_sales and _complaint_severity == "high":
                for item in _LEAD_DISCOVERY_SUPPRESSIONS:
                    if item not in forbidden:
                        forbidden.append(item)
            for _cf in _complaint_forbidden:
                if _cf not in forbidden:
                    forbidden.append(_cf)

        facts_tag = (
            f"planner:acknowledge_facts:{len(facts)}" if facts else "planner:acknowledge_facts:none"
        )
        audit: list[str] = [
            facts_tag,
            f"planner:must_not_mention:{len(forbidden)}",
            f"planner:primary_goal:{goal}",
            f"planner:next_question:{nq}" if nq else "planner:next_question:none",
        ]
        if summary:
            audit.append("planner:tool_summary:set")

        return ResponsePlan(
            acknowledge_facts=facts,
            primary_goal=goal,
            next_question=nq,
            must_not_mention=forbidden,
            max_questions=max_questions,
            tone="warm_consultative",
            customer_safe_tool_summary=summary,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Public helpers (named per spec)
# ---------------------------------------------------------------------------


def _acknowledge_facts(context_pack: ContextPack) -> list[str]:
    """Return a list of known project facts to acknowledge in the response."""
    facts: list[str] = []

    # Project-event guidance (structured, not customer-facing text).
    if context_pack.project_event == "new_project":
        facts.append("project_scope: new_project")
    elif context_pack.project_event == "same_project_service_addition":
        facts.append("project_scope: service_addition")
    elif context_pack.project_event == "project_switch":
        facts.append("project_scope: returning_to_previous")

    if context_pack.active_service:
        facts.append(f"active_service: {context_pack.active_service}")

    for fact in context_pack.known_facts:
        if fact.label in {"genre", "manuscript_status", "word_count", "page_count"}:
            facts.append(f"{fact.label}: {fact.value}")

    # Phase 13: attachment acknowledgement.
    if context_pack.attachments_received:
        cats = [a.category or "other" for a in context_pack.attachments_received]
        facts.append(f"attachment_received: {', '.join(cats)}")
        if context_pack.assessment_type:
            facts.append(f"assessment_type: {context_pack.assessment_type}")
        if context_pack.specialist_role:
            facts.append(f"specialist_role: {context_pack.specialist_role}")

    return facts


def _must_not_mention(
    context_pack: ContextPack,
    *,
    negation_targets: list[Any] | None = None,
    portfolio_fallback_decision: Any | None = None,
) -> list[str]:
    """Return topics and terms that must not appear in the customer-facing reply."""
    items: list[str] = []

    # Context-derived suppressions.
    items.extend(context_pack.forbidden_reasks)

    # Permanent internal-term suppressions.
    items.extend(_INTERNAL_TERMS)

    # Suppress unrelated service drift when a service focus is established.
    if context_pack.active_service:
        items.append("unrelated_service_drift")

    # Suppress delegated/declined slot question forms.
    all_resolved = (
        list(context_pack.declined_slots or [])
        + list(context_pack.delegated_slots or [])
        + list(context_pack.unknown_slots or [])
    )
    for s in all_resolved:
        if s.forbidden_reask and s.slot not in items:
            items.append(s.slot)

    # Suppress explicitly negated services/actions from the response.
    if negation_targets:
        for t in negation_targets:
            if getattr(t, "polarity", None) == "negated":
                tt = getattr(t, "target_type", "")
                tv = getattr(t, "target", "")
                if tt == "service" and tv:
                    items.append(tv)
                elif tt in ("tool_action", "document") and tv:
                    items.append(tv)

    # Phase 13: suppress attachment content-analysis phrases.
    if context_pack.attachments_received:
        for phrase in _ATTACHMENT_FORBIDDEN_PHRASES:
            if phrase not in items:
                items.append(phrase)

    # Suppress portfolio genre/category filter question when fallback is active.
    if portfolio_fallback_decision is not None:
        strategy = getattr(portfolio_fallback_decision, "strategy", None)
        if strategy in ("fallback_general_samples", "fallback_service_samples"):
            for _suppressed in ("genre", "category", "portfolio_filter"):
                if _suppressed not in items:
                    items.append(_suppressed)

    return _ordered_unique(items)


def _primary_goal(
    intent: IntentVote,
    context_pack: ContextPack,
    tool_governance: ToolGovernanceDecision | None,
    portfolio_fallback_decision: Any | None = None,
    flexible_intent_decision: Any | None = None,
    lead_objective_decision: LeadObjectiveDecision | None = None,
    *,
    consultation_objective_decision: Any | None = None,
    current_question_priority: Any | None = None,
) -> str:
    """Determine the high-level goal for this response turn."""
    # PR 2 — Consultation-first overrides (highest priority).
    if consultation_objective_decision is not None:
        cod_move = getattr(consultation_objective_decision, "objective_move", None)
        cod_goal = getattr(consultation_objective_decision, "recommended_primary_goal", None)
        if cod_move == "create_consultation_handoff":
            return "consultation_handoff_confirmation"
        if cod_move == "ask_preferred_call_time":
            return "consultation_time_capture"
        if cod_move == "answer_then_consultation":
            return "answer_current_question"
        if cod_goal:
            return str(cod_goal)

    if context_pack.lead_created or (
        lead_objective_decision is not None and lead_objective_decision.stage == "lead_created"
    ):
        return "lead_created_confirmation"

    # Greeting-only turns always map to greeting_welcome regardless of classifier output.
    if getattr(context_pack, "is_greeting_turn", False):
        return "greeting_welcome"

    if lead_objective_decision is not None and lead_objective_decision.stop_discovery:
        if lead_objective_decision.recommended_primary_goal:
            return lead_objective_decision.recommended_primary_goal
        if lead_objective_decision.objective_move in {
            "offer_consultation",
            "schedule_consultation",
        }:
            return "consultation_handoff"
        if lead_objective_decision.objective_move == "handoff_to_specialist":
            return "specialist_handoff"
        if lead_objective_decision.objective_move in {"ask_contact", "create_lead"}:
            return "lead_contact_capture"

    if tool_governance is not None and not tool_governance.allowed:
        if "counterfactual" in tool_governance.reason:
            return "clarify_intent"
        return "safe_blocked_action"

    # Project-event overrides (evaluated before service/intent goals).
    if context_pack.project_event == "ambiguous_project_reference":
        return "clarify_project_scope"

    # Phase 13: attachment intake overrides.
    if context_pack.attachments_received:
        if context_pack.assessment_type:
            return "assessment_handoff"
        return "attachment_received_assessment"

    # Step 3 (tone fix): honour recommended_primary_goal from lead_objective even
    # when stop_discovery=False (welcome-first and answer-before-ask rules).
    if (
        lead_objective_decision is not None
        and not lead_objective_decision.stop_discovery
        and lead_objective_decision.recommended_primary_goal
        and lead_objective_decision.recommended_primary_goal
        in {"greeting_welcome", "answer_current_question"}
    ):
        return lead_objective_decision.recommended_primary_goal

    # Portfolio fallback goal overrides.
    if portfolio_fallback_decision is not None:
        strategy = getattr(portfolio_fallback_decision, "strategy", None)
        if strategy == "ask_filter_once":
            return "portfolio_scoping"
        if strategy in (
            "fallback_general_samples",
            "fallback_service_samples",
            "use_context_filter",
        ):
            return "portfolio_matching"

    # Flexible intent overrides — evaluated before service-specific goals.
    if flexible_intent_decision is not None and getattr(
        flexible_intent_decision, "detected", False
    ):
        return str(
            getattr(flexible_intent_decision, "recommended_primary_goal", "continue_discovery")
        )

    # Delegated creative slot: cover_style handed off to BookCraft.
    delegated_slot_names = {s.slot for s in (context_pack.delegated_slots or [])}
    if (
        "cover_style" in delegated_slot_names
        and context_pack.active_service == "cover_design_illustration"
    ):
        return "process_explanation"

    if context_pack.active_service == "cover_design_illustration":
        return "cover_design_scoping"

    return _GOAL_BY_QUERY.get(intent.query_primary.value, "continue_discovery")


def _next_question(
    intent: IntentVote,
    context_pack: ContextPack,
    primary_goal: str,
    flexible_intent_decision: Any | None = None,
    lead_objective_decision: LeadObjectiveDecision | None = None,
    contact_capture_result: ContactCaptureResult | None = None,
    *,
    consultation_objective_decision: Any | None = None,
) -> str | None:
    """Return the single highest-priority missing fact to ask about next."""
    del intent  # reserved for future per-intent refinement

    # Blocked or ambiguous turns: do not auto-issue a follow-up question.
    if primary_goal in {"safe_blocked_action", "clarify_intent"}:
        return None

    # PR 2 — Consultation-first next questions.
    if primary_goal == "consultation_handoff_confirmation":
        return None
    if primary_goal == "consultation_time_capture":
        return "preferred_call_time"
    if primary_goal == "answer_current_question":
        # After answering, offer consultation — not a scoping question.
        nq = (
            getattr(consultation_objective_decision, "next_question", None)
            if consultation_objective_decision is not None
            else None
        )
        return nq or "consultation_interest"

    if primary_goal == "lead_created_confirmation":
        return None

    # Greeting-only turns: ask how we can help, never scoping.
    if primary_goal == "greeting_welcome":
        return "how_can_we_help"

    # Gap 1: long-tail goal next-question rules — none of these should trigger scoping.
    if primary_goal == "revision_response":
        return None  # Answer the revision question; don't scope
    if primary_goal == "payment_guidance":
        return None  # Buying-stage signal; route to contact, not scoping
    if primary_goal == "celebrate_and_advance":
        return None  # Celebrate the milestone; offer the natural next step
    if primary_goal == "complaint_recovery":
        return None  # Complaint: acknowledge and offer handoff, no scoping
    if primary_goal == "gentle_clarify":
        return "clarify_intent"  # Ask one warm clarifying question
    if primary_goal == "minimal_acknowledge":
        return None  # Spam/abuse: short acknowledgment, no engagement
    if primary_goal == "friendly_redirect":
        return None  # Off-topic: warm redirect to BookCraft services

    if lead_objective_decision is not None and lead_objective_decision.stop_discovery:
        if contact_capture_result is not None and contact_capture_result.lead_contact_ready:
            # PR 2: contact ready — ask for call time instead of no-op.
            if not context_pack.preferred_call_time:
                return "preferred_call_time"
            return None
        return lead_objective_decision.next_question or _contact_next_question(context_pack)

    # Project-scope clarification: ask Claude to resolve same vs. new project.
    if primary_goal == "clarify_project_scope":
        return _PROJECT_CLARIFICATION_QUESTION

    # Delegated creative turn: no next question for the delegated slot.
    if primary_goal == "process_explanation":
        return None

    # Phase 13: attachment assessment — do not ask a follow-up content question.
    if primary_goal in ("assessment_handoff", "attachment_received_assessment"):
        return "consultation_interest"

    # Flexible intent goals: use the decision's suggested next question.
    if primary_goal in (
        "flexible_service_guidance",
        "bookcraft_discretion",
        "consultation_handoff",
    ):
        if flexible_intent_decision is not None:
            nq_raw = getattr(flexible_intent_decision, "next_question", None)
            nq = str(nq_raw) if nq_raw is not None else None
            # Ensure the suggested question is not in disallowed_next_questions.
            disallowed = set(context_pack.disallowed_next_questions)
            if nq and nq not in disallowed:
                return nq
        return None

    # Portfolio scoping: ask for service/genre filter once.
    if primary_goal == "portfolio_scoping":
        return "portfolio_filter"

    # Portfolio matching: no genre/category filter question after fallback.
    if primary_goal == "portfolio_matching":
        return None

    # Select the priority list for this scenario.
    if primary_goal == "cover_design_scoping":
        priority = _COVER_DESIGN_PRIORITY
    elif primary_goal == "pricing_scoping":
        priority = _PRICING_PRIORITY
    else:
        priority = _DEFAULT_PRIORITY

    # Candidate pool: union of allowed_next_questions and missing_facts.
    available: set[str] = set(context_pack.missing_facts) | set(context_pack.allowed_next_questions)

    for fact in priority:
        if fact in available:
            return fact

    # Fall back to whatever the ContextPack surfaced first.
    if context_pack.allowed_next_questions:
        return context_pack.allowed_next_questions[0]
    if context_pack.missing_facts:
        return context_pack.missing_facts[0]

    return None


# ---------------------------------------------------------------------------
# Tool-summary helper (used internally by plan())
# ---------------------------------------------------------------------------


def _customer_safe_tool_summary(
    tool_governance: ToolGovernanceDecision | None,
    action_result: Any | None,
) -> str | None:
    if tool_governance is not None and not tool_governance.allowed:
        if tool_governance.blocked_message:
            return tool_governance.blocked_message

    if action_result is not None:
        summary = getattr(action_result, "customer_safe_summary", None)
        if summary and isinstance(summary, str):
            return summary

    return None


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _contact_next_question(context_pack: ContextPack) -> str:
    """Return the most specific contact question given what's already captured.

    Phone is required for consultation; email is optional.
    Avoids re-asking fields already in known_facts.
    """
    known_paths = {kf.path for kf in context_pack.known_facts}
    has_name = "personal.name" in known_paths
    has_phone = "personal.phone" in known_paths
    has_email = "personal.email" in known_paths

    if not has_name:
        return "name_and_email_or_phone"
    if not has_phone:
        # Phone is required — ask for it even when email is already captured.
        return "missing_phone"
    if not has_email:
        return "missing_email"
    # All captured — move to call time.
    return "preferred_call_time"


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
