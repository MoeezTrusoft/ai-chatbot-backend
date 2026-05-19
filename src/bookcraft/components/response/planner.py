from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.tools.governance import ToolGovernanceDecision
from bookcraft.domain.state import ThreadState

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
    "pricing_question": "pricing_scoping",
    "timeline_question": "pricing_scoping",
    "consultation_request": "consultation_scoping",
    "nda_request": "document_scoping",
    "agreement_request": "document_scoping",
    "portfolio_request": "portfolio_matching",
}

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
    ) -> ResponsePlan:
        del state, action_plan  # all project state surfaces via context_pack

        facts = _acknowledge_facts(context_pack)
        forbidden = _must_not_mention(context_pack)
        goal = _primary_goal(intent, context_pack, tool_governance)
        nq = _next_question(intent, context_pack, goal)
        summary = _customer_safe_tool_summary(tool_governance, action_result)

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
            max_questions=1,
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

    return facts


def _must_not_mention(context_pack: ContextPack) -> list[str]:
    """Return topics and terms that must not appear in the customer-facing reply."""
    items: list[str] = []

    # Context-derived suppressions.
    items.extend(context_pack.forbidden_reasks)

    # Permanent internal-term suppressions.
    items.extend(_INTERNAL_TERMS)

    # Suppress unrelated service drift when a service focus is established.
    if context_pack.active_service:
        items.append("unrelated_service_drift")

    return _ordered_unique(items)


def _primary_goal(
    intent: IntentVote,
    context_pack: ContextPack,
    tool_governance: ToolGovernanceDecision | None,
) -> str:
    """Determine the high-level goal for this response turn."""
    if tool_governance is not None and not tool_governance.allowed:
        if "counterfactual" in tool_governance.reason:
            return "clarify_intent"
        return "safe_blocked_action"

    # Project-event overrides (evaluated before service/intent goals).
    if context_pack.project_event == "ambiguous_project_reference":
        return "clarify_project_scope"

    if context_pack.active_service == "cover_design_illustration":
        return "cover_design_scoping"

    return _GOAL_BY_QUERY.get(intent.query_primary.value, "continue_discovery")


def _next_question(
    intent: IntentVote,
    context_pack: ContextPack,
    primary_goal: str,
) -> str | None:
    """Return the single highest-priority missing fact to ask about next."""
    del intent  # reserved for future per-intent refinement

    # Blocked or ambiguous turns: do not auto-issue a follow-up question.
    if primary_goal in {"safe_blocked_action", "clarify_intent"}:
        return None

    # Project-scope clarification: ask Claude to resolve same vs. new project.
    if primary_goal == "clarify_project_scope":
        return _PROJECT_CLARIFICATION_QUESTION

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


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
