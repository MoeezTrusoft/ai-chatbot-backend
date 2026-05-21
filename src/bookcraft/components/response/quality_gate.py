from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.assumption_guard import AssumptionGuard
from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.planner import ResponsePlan
from bookcraft.components.response.style_policy import ResponseStylePolicy, SalesToneReport
from bookcraft.components.tools.governance import ToolGovernanceDecision
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState

_ASSUMPTION_GUARD = AssumptionGuard()

# Module-level style-policy instance used to drive slippy-word detection.
_STYLE_POLICY = ResponseStylePolicy.default()

# ---------------------------------------------------------------------------
# Compiled patterns — module-level for reuse
# ---------------------------------------------------------------------------

# Internal implementation terms that must never appear in customer-facing text.
_ARTIFACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("backend", re.compile(r"\bbackend\b", re.IGNORECASE)),
    ("classifier", re.compile(r"\bclassifier\b", re.IGNORECASE)),
    ("runtime atoms", re.compile(r"\bruntime\s+atoms\b", re.IGNORECASE)),
    ("provider votes", re.compile(r"\bprovider\s+votes\b", re.IGNORECASE)),
    ("RAG", re.compile(r"\bRAG\b")),
    ("tool_governance", re.compile(r"\btool_governance\b", re.IGNORECASE)),
    ("action_plan", re.compile(r"\baction_plan\b", re.IGNORECASE)),
    ("deterministic engine", re.compile(r"\bdeterministic\s+engine\b", re.IGNORECASE)),
    ("quote engine", re.compile(r"\bquote\s+engine\b", re.IGNORECASE)),
    ("approved engine", re.compile(r"\bapproved\s+engine\b", re.IGNORECASE)),
    ("tool output", re.compile(r"\btool\s+output\b", re.IGNORECASE)),
    ("Source:", re.compile(r"\bSource:\s", re.IGNORECASE)),
    ("Context:", re.compile(r"\bContext:\s", re.IGNORECASE)),
    ("Action plan:", re.compile(r"\bAction\s+plan:\s", re.IGNORECASE)),
]

# Price-figure patterns — unapproved price mentions.
_PRICE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\$\s*\d", re.IGNORECASE),
    re.compile(r"£\s*\d", re.IGNORECASE),
    re.compile(r"€\s*\d", re.IGNORECASE),
    re.compile(r"\bUSD\s*\d", re.IGNORECASE),
    re.compile(r"\b\d[\d,]*\s*(?:usd|gbp|eur|dollars?|pounds?|euros?)\b", re.IGNORECASE),
    # Price ranges like 1,500-2,500 when preceded by $ or currency words.
    re.compile(
        r"(?:\$|USD\s*)\d[\d,]*\s*[-–]\s*\d",
        re.IGNORECASE,
    ),
]

# Committed timeline promises — exact deliveries without an approved quote.
_TIMELINE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:in|within|after|takes?|ready\s+in|delivered?\s+in|completed\s+in|"
        r"finished\s+in|done\s+in|guaranteed\s+in|by)\s+\d+\s*"
        r"(?:[-–]\s*\d+\s*|to\s+\d+\s*)?(?:business\s+)?(?:day|days|week|weeks|month|months)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d+\s*(?:[-–]\s*\d+\s*|to\s+\d+\s*)?(?:business\s+)?"
        r"(?:day|days|week|weeks|month|months)\s+"
        r"(?:turnaround|delivery|lead\s+time|timeline|schedule)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwill\s+take\s+\d+", re.IGNORECASE),
    re.compile(r"\bguaranteed\s+(?:in|within)\s+\d+", re.IGNORECASE),
]

# Markdown / structural formatting artifacts.
_FORMATTING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("heading", re.compile(r"^\s*#{1,6}\s", re.MULTILINE)),
    ("table", re.compile(r"\n\s*\|.*\|")),
    ("three_plus_bullets", re.compile(r"(?:^\s*[-*]\s+.+\n){3}", re.MULTILINE)),
    ("code_fence", re.compile(r"^\s*```", re.MULTILINE)),
]

# Slippy / hedging words — built from the style policy so there is one source
# of truth.  More than _SLIPPY_LIMIT total instances signals a problem.
_SLIPPY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
    for phrase in _STYLE_POLICY.weak_phrases
] or [
    # Fallback in case the policy is somehow empty.
    re.compile(r"\bmaybe\b", re.IGNORECASE),
    re.compile(r"\bpossibly\b", re.IGNORECASE),
    re.compile(r"\bprobably\b", re.IGNORECASE),
]
_SLIPPY_LIMIT = 2  # > this many instances = excessive

# Known-fact re-ask patterns keyed on the forbidden-reask label.
_REASK_PATTERNS: dict[str, re.Pattern[str]] = {
    "genre": re.compile(r"\b(?:what|which)\s+genre\b", re.IGNORECASE),
    "what genre": re.compile(r"\b(?:what|which)\s+genre\b", re.IGNORECASE),
    "manuscript_stage": re.compile(
        r"\b(?:what\s+stage|manuscript\s+stage|starting\s+from\s+scratch|have\s+a\s+draft)\b",
        re.IGNORECASE,
    ),
    "draft status": re.compile(
        r"\b(?:what\s+stage|manuscript\s+stage|starting\s+from\s+scratch|have\s+a\s+draft)\b",
        re.IGNORECASE,
    ),
    "starting from scratch": re.compile(r"\bstarting\s+from\s+scratch\b", re.IGNORECASE),
    # Slot-resolution re-ask patterns (also used for delegated slot checks).
    "cover_style": re.compile(
        r"\b(?:cover\s+style|visual\s+direction|design\s+idea|cover\s+illustration)\b",
        re.IGNORECASE,
    ),
    "word_or_page_count": re.compile(
        r"\b(?:word\s+count|page\s+count|how\s+many\s+(?:words|pages)|word\s+or\s+page)\b",
        re.IGNORECASE,
    ),
    "deadline": re.compile(
        r"\b(?:deadline|launch\s+(?:date|window|timeline)|by\s+when|target\s+(?:date|deadline))\b",
        re.IGNORECASE,
    ),
    # Portfolio filter re-ask — caught after fallback_allowed.
    "portfolio_filter": re.compile(
        r"\b(?:what\s+genre|which\s+genre|what\s+(?:type|kind)\s+of\s+(?:book|samples?)"
        r"|what\s+service|which\s+service|what\s+category)\b",
        re.IGNORECASE,
    ),
}

# Slot-resolution re-ask patterns for delegated/declined/unknown slots.
_DELEGATED_SLOT_REASK_PATTERNS = _REASK_PATTERNS  # same dict, reused for clarity

# Success-claim patterns — must not appear when a tool action was blocked.
_SUCCESS_CLAIM_RE = re.compile(
    r"\b(?:scheduled|booked|confirmed|created|sent|generated|produced|"
    r"completed|done|ready|your\s+appointment|your\s+nda|your\s+agreement)\b",
    re.IGNORECASE,
)

# Next-step progression phrases (alternative to a literal `?`).
_PROGRESSION_RE = re.compile(
    r"\b(?:let\s+me\s+know|share|tell\s+me|could\s+you|what\s+(?:is|are)|"
    r"which|would\s+you|are\s+you|do\s+you|have\s+you)\b",
    re.IGNORECASE,
)

# Intents for which pricing output is approved.
_PRICING_INTENTS = {QueryIntentType.PRICING_QUESTION, QueryIntentType.TIMELINE_QUESTION}

# Internal terms to exclude from the safe fallback text.
_INTERNAL_WORDS = {
    "backend",
    "classifier",
    "runtime atoms",
    "provider votes",
    "RAG",
    "tool_governance",
    "action_plan",
    "deterministic engine",
    "quote engine",
}


class ResponseQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    failures: list[str] = Field(default_factory=list)
    repair_instructions: str | None = None
    safe_repair_context: dict[str, object] | None = None
    safe_fallback: str | None = None
    sales_tone: SalesToneReport | None = None
    audit: list[str] = Field(default_factory=list)


class ResponseQualityGate:
    """Final response verification layer.

    Evaluates generated text against plan, context, and governance decision
    before it reaches the customer. Never calls an external model.
    """

    def __init__(self, *, style_policy: ResponseStylePolicy | None = None) -> None:
        self.style_policy = style_policy or ResponseStylePolicy.default()

    def evaluate(
        self,
        *,
        text: str,
        intent: IntentVote,
        state: ThreadState,
        context_pack: ContextPack | None = None,
        response_plan: ResponsePlan | None = None,
        tool_governance: ToolGovernanceDecision | None = None,
    ) -> ResponseQualityReport:
        del state  # reserved for future checks
        failures: list[str] = []
        audit: list[str] = []

        # Check 1 — Internal artifact leak.
        artifacts = _internal_artifacts(text)
        if artifacts:
            failures.append(f"internal_artifact_leak:{','.join(artifacts[:3])}")
            audit.append(f"quality:internal_artifact:{len(artifacts)}")
        else:
            audit.append("quality:internal_artifact:clean")

        # Check 2 — Known-fact re-ask.
        reasks = _known_fact_reasks(text, context_pack)
        if reasks:
            failures.append(f"known_fact_reask:{','.join(reasks)}")
            audit.append(f"quality:known_fact_reask:violated={reasks}")
        else:
            audit.append("quality:known_fact_reask:clean")

        # Check 3 — Wrong service mention.
        wrong = _wrong_service_mentions(text, context_pack, response_plan)
        if wrong:
            failures.append(f"wrong_service_mention:{','.join(wrong)}")
            audit.append(f"quality:wrong_service:detected={wrong}")
        else:
            audit.append("quality:wrong_service:clean")

        # Check 4 — Question count.
        count = _question_count(text)
        max_q = response_plan.max_questions if response_plan is not None else 1
        if count > max_q:
            failures.append(f"too_many_questions:{count}_exceeds_max_{max_q}")
            audit.append(f"quality:question_count:{count}:FAIL")
        else:
            audit.append(f"quality:question_count:{count}:max={max_q}:ok")

        # Check 4b — stop-discovery mode must avoid additional scoping loops.
        if _stop_discovery_multi_scope_failure(text, response_plan):
            failures.append("stop_discovery_multi_scope_questions")
            audit.append("quality:stop_discovery_scope:FAIL")
        else:
            audit.append("quality:stop_discovery_scope:ok")

        # Check 4c — once lead is created, no further discovery questions.
        if _lead_created_discovery_failure(text, response_plan):
            failures.append("lead_created_discovery_question")
            audit.append("quality:lead_created_discovery:FAIL")
        else:
            audit.append("quality:lead_created_discovery:ok")

        # Check 4d — never demand both email and phone.
        if _demands_both_email_and_phone(text):
            failures.append("demands_both_email_and_phone")
            audit.append("quality:contact_demand_both:FAIL")
        else:
            audit.append("quality:contact_demand_both:ok")

        # Check 5 — Unapproved price figures.
        price_hits = _unapproved_price_mentions(text, intent, response_plan, tool_governance)
        if price_hits:
            failures.append("unapproved_price_figure")
            audit.append(f"quality:unapproved_price:FAIL:{price_hits[0][:20]}")
        else:
            audit.append("quality:unapproved_price:clean")

        # Check 6 — Unapproved committed timelines.
        timeline_hits = _unapproved_timeline_mentions(text, intent, response_plan, tool_governance)
        if timeline_hits:
            failures.append("unapproved_committed_timeline")
            audit.append("quality:unapproved_timeline:FAIL")
        else:
            audit.append("quality:unapproved_timeline:clean")

        # Check 7 — Markdown / structural formatting.
        fmt_hits = _formatting_artifacts(text)
        if fmt_hits:
            failures.append("markdown_formatting_detected")
            audit.append(f"quality:markdown:{len(fmt_hits)}_patterns")
        else:
            audit.append("quality:markdown:clean")

        # Check 8 — Excessive slippy language.
        slippy = _slippy_word_hits(text)
        if len(slippy) > _SLIPPY_LIMIT:
            failures.append(f"excessive_weak_language:{len(slippy)}_instances")
            audit.append(f"quality:weak_language:{len(slippy)}:FAIL")
        else:
            audit.append(f"quality:weak_language:{len(slippy)}:ok")

        # Check 9 — Missing next step.
        if _missing_next_step(text, response_plan):
            failures.append("missing_next_step_question")
            nq = response_plan.next_question if response_plan else None
            audit.append(f"quality:missing_next_step:FAIL:expected={nq}")
        else:
            audit.append("quality:missing_next_step:ok")

        # Check 10 — Blocked tool safety.
        if _blocked_tool_mismatch(text, tool_governance):
            failures.append("blocked_action_claimed_as_success")
            audit.append("quality:blocked_tool_safety:FAIL")
        else:
            audit.append("quality:blocked_tool_safety:ok")

        # Check 11 — Delegated / declined slot re-ask.
        delegated_reasks = _delegated_slot_reasks(text, context_pack)
        if delegated_reasks:
            failures.append(f"delegated_slot_reask:{','.join(delegated_reasks)}")
            audit.append(f"quality:delegated_slot_reask:FAIL:{delegated_reasks}")
        else:
            audit.append("quality:delegated_slot_reask:clean")

        # Check 12 — Attachment content-review claim (Phase 13).
        attachment_review_hits = _attachment_content_review_claims(text, context_pack)
        if attachment_review_hits:
            failures.append(f"attachment_content_reviewed:{attachment_review_hits[0][:40]}")
            audit.append(f"quality:attachment_review_claim:FAIL:{attachment_review_hits[0][:30]}")
        else:
            audit.append("quality:attachment_review_claim:clean")

        # Check 13 — Assumption leaks and greeting scoping violations (PR: coherence).
        assumption_failures = _ASSUMPTION_GUARD.check_response(
            text=text,
            context_pack=context_pack,
            response_plan=response_plan,
        )
        if assumption_failures:
            failures.extend(assumption_failures)
            audit.append(f"quality:assumption_guard:FAIL:{assumption_failures[0][:50]}")
        else:
            audit.append("quality:assumption_guard:clean")

        # Check 14 — Consultation-first: contact-only response when priority question active.
        if _contact_only_when_priority_active(text, response_plan):
            failures.append("contact_only_without_answering_priority_question")
            audit.append("quality:consultation_first:contact_only_violation:FAIL")
        else:
            audit.append("quality:consultation_first:contact_only_check:ok")

        # Check 15 — Wrong scoping after contact captured (should ask call time, not genre).
        if _wrong_scoping_after_contact_ready(text, context_pack):
            failures.append("scoping_question_after_contact_ready")
            audit.append("quality:consultation_first:wrong_scoping_after_contact:FAIL")
        else:
            audit.append("quality:consultation_first:scoping_after_contact:ok")

        # Check 16 — Attachment turn must not ask manuscript stage / draft status before handoff.
        if _attachment_asks_manuscript_stage(text, context_pack):
            failures.append("attachment_turn_asks_manuscript_stage_before_handoff")
            audit.append("quality:attachment_priority:manuscript_stage_asked:FAIL")
        else:
            audit.append("quality:attachment_priority:manuscript_stage_check:ok")

        # Check 17 — Attachment turn must not ask word/page count before handoff.
        if _attachment_asks_word_count(text, context_pack):
            failures.append("attachment_turn_asks_word_count_before_handoff")
            audit.append("quality:attachment_priority:word_count_asked:FAIL")
        else:
            audit.append("quality:attachment_priority:word_count_check:ok")

        sales_tone_report = self.style_policy.evaluate(
            text=text,
            response_plan=response_plan,
            context_pack=context_pack,
        )
        if not sales_tone_report.passed:
            failures.append("sales_tone")
            audit.append(f"quality:sales_tone:FAIL:{','.join(sales_tone_report.failures[:3])}")
        else:
            audit.append("quality:sales_tone:ok")
        audit.extend(f"sales_tone:{entry}" for entry in sales_tone_report.audit)

        passed = len(failures) == 0
        audit.append(f"quality_gate:passed={passed}:failures={len(failures)}")

        repair_instructions: str | None = None
        safe_fallback: str | None = None

        safe_repair_context: dict[str, object] | None = None
        if not passed:
            repair_instructions = _build_repair_instructions(
                failures,
                sales_tone_report.suggestions,
            )
            safe_repair_context = _build_safe_repair_context(
                failures=failures,
                context_pack=context_pack,
                response_plan=response_plan,
                tool_governance=tool_governance,
            )
            safe_fallback = _build_safe_fallback(
                failures=failures,
                context_pack=context_pack,
                response_plan=response_plan,
                tool_governance=tool_governance,
                intent=intent,
            )
            fallback_tone = self.style_policy.evaluate(
                text=safe_fallback,
                response_plan=response_plan,
                context_pack=context_pack,
            )
            if not fallback_tone.passed:
                safe_fallback = _build_style_safe_fallback(
                    context_pack=context_pack,
                    response_plan=response_plan,
                    intent=intent,
                )
                audit.append("quality:safe_fallback:style_rewritten")
            else:
                audit.append("quality:safe_fallback:style_ok")

        return ResponseQualityReport(
            passed=passed,
            failures=failures,
            repair_instructions=repair_instructions,
            safe_repair_context=safe_repair_context,
            safe_fallback=safe_fallback,
            sales_tone=sales_tone_report,
            audit=audit,
        )


def _build_safe_repair_context(
    *,
    failures: list[str],
    context_pack: ContextPack | None,
    response_plan: ResponsePlan | None,
    tool_governance: ToolGovernanceDecision | None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "repair_goal": (
            "Rewrite the response to remove quality failures and keep customer-facing "
            "guidance clear."
        ),
        "must_keep": [],
        "must_not_ask": [],
    }
    if response_plan is not None:
        if response_plan.acknowledge_facts:
            context["must_keep"] = response_plan.acknowledge_facts
        if response_plan.next_question is not None:
            context["next_question"] = response_plan.next_question
        if response_plan.customer_safe_tool_summary:
            context["tool_summary"] = response_plan.customer_safe_tool_summary
    if context_pack is not None:
        if context_pack.forbidden_reasks:
            context["must_not_ask"] = context_pack.forbidden_reasks
        if context_pack.active_service is not None:
            context["active_service"] = context_pack.active_service
        if context_pack.active_genre is not None:
            context["active_genre"] = context_pack.active_genre
        if context_pack.manuscript_status is not None:
            context["manuscript_status"] = context_pack.manuscript_status
    if tool_governance is not None and tool_governance.blocked_message:
        context["blocked_message"] = tool_governance.blocked_message
    context["failures"] = failures
    return context


# ---------------------------------------------------------------------------
# Check helpers (pure functions returning list[str] or bool)
# ---------------------------------------------------------------------------


def _internal_artifacts(text: str) -> list[str]:
    """Return labels for every internal implementation term found in text."""
    return [label for label, pattern in _ARTIFACT_PATTERNS if pattern.search(text)]


def _known_fact_reasks(text: str, context_pack: ContextPack | None) -> list[str]:
    """Return forbidden-reask labels whose patterns appear in text."""
    if context_pack is None:
        return []
    violated: list[str] = []
    for label in context_pack.forbidden_reasks:
        pattern = _REASK_PATTERNS.get(label)
        if pattern is not None and pattern.search(text):
            violated.append(label)
    return violated


def _wrong_service_mentions(
    text: str,
    context_pack: ContextPack | None,
    response_plan: ResponsePlan | None,
) -> list[str]:
    """Return wrong service names that appear in text given the active service context."""
    if context_pack is None or context_pack.active_service is None:
        return []

    # Build set of services explicitly allowed by plan acknowledgements.
    allowed_extras: set[str] = set()
    if response_plan is not None:
        for fact in response_plan.acknowledge_facts:
            if "service" in fact:
                allowed_extras.add(fact.split(":")[-1].strip())

    wrong: list[str] = []
    if context_pack.active_service == "cover_design_illustration":
        if re.search(r"\bghostwriting\b", text, re.IGNORECASE):
            if "ghostwriting" not in allowed_extras:
                wrong.append("ghostwriting_when_cover_design_active")

    return wrong


def _question_count(text: str) -> int:
    """Count real question marks in text (abbreviations do not use '?')."""
    return text.count("?")


def _unapproved_price_mentions(
    text: str,
    intent: IntentVote,
    response_plan: ResponsePlan | None,  # reserved for future goal-based allowances
    tool_governance: ToolGovernanceDecision | None,
) -> list[str]:
    """Return price-pattern matches when the context does not approve them."""
    del response_plan  # reserved
    hits = [p.pattern for p in _PRICE_PATTERNS if p.search(text)]
    if not hits:
        return []

    # Prices are approved only when the intent is pricing/timeline AND
    # governance has not blocked the action.
    if intent.query_primary in _PRICING_INTENTS and (
        tool_governance is None or tool_governance.allowed
    ):
        return []

    return hits


def _unapproved_timeline_mentions(
    text: str,
    intent: IntentVote,
    response_plan: ResponsePlan | None,  # reserved for future goal-based allowances
    tool_governance: ToolGovernanceDecision | None,
) -> list[str]:
    """Return timeline-pattern matches when the context does not approve them."""
    del response_plan  # reserved
    hits = [p.pattern for p in _TIMELINE_PATTERNS if p.search(text)]
    if not hits:
        return []

    if intent.query_primary in _PRICING_INTENTS and (
        tool_governance is None or tool_governance.allowed
    ):
        return []

    return hits


def _formatting_artifacts(text: str) -> list[str]:
    """Return formatting artifact labels found in text."""
    return [label for label, pattern in _FORMATTING_PATTERNS if pattern.search(text)]


def _slippy_word_hits(text: str) -> list[str]:
    """Return every individual slippy-word match (not just the distinct phrases)."""
    hits: list[str] = []
    for pattern in _SLIPPY_PATTERNS:
        hits.extend(m.group(0) for m in pattern.finditer(text))
    return hits


def _missing_next_step(text: str, response_plan: ResponsePlan | None) -> bool:
    """Return True when a next question was planned but the response lacks one."""
    if response_plan is None or response_plan.next_question is None:
        return False
    has_question = "?" in text
    has_progression = bool(_PROGRESSION_RE.search(text))
    return not has_question and not has_progression


def _blocked_tool_mismatch(
    text: str,
    tool_governance: ToolGovernanceDecision | None,
) -> bool:
    """Return True when the response claims a blocked action succeeded."""
    if tool_governance is None or tool_governance.allowed:
        return False
    return bool(_SUCCESS_CLAIM_RE.search(text))


def _stop_discovery_multi_scope_failure(text: str, response_plan: ResponsePlan | None) -> bool:
    if response_plan is None or response_plan.primary_goal not in {
        "lead_contact_capture",
        "consultation_handoff",
        "specialist_handoff",
    }:
        return False
    lowered = text.casefold()
    scope_topics = (
        "genre",
        "word count",
        "page count",
        "deadline",
        "timeline",
        "cover style",
        "manuscript stage",
    )
    hit_count = sum(1 for topic in scope_topics if topic in lowered)
    return hit_count >= 2


def _lead_created_discovery_failure(text: str, response_plan: ResponsePlan | None) -> bool:
    if response_plan is None or response_plan.primary_goal != "lead_created_confirmation":
        return False
    lowered = text.casefold()
    discovery_markers = (
        "what genre",
        "word count",
        "page count",
        "deadline",
        "cover style",
        "manuscript stage",
    )
    return any(marker in lowered for marker in discovery_markers)


def _demands_both_email_and_phone(text: str) -> bool:
    lowered = text.casefold()
    if "email or phone" in lowered or "email or a phone" in lowered:
        return False
    if "email and phone" in lowered:
        return True
    return bool(re.search(r"\bboth\s+(?:your\s+)?email\s+and\s+(?:your\s+)?phone\b", lowered))


_ATTACHMENT_REVIEW_CLAIM_RE = re.compile(
    r"\b(?:i\s+(?:have\s+)?(?:reviewed|read|analyzed|analysed|checked|examined|"
    r"inspected|assessed|gone\s+through|looked\s+at|went\s+through)|"
    r"after\s+(?:reviewing|reading|analyzing|checking|examining|going\s+through)|"
    r"having\s+(?:reviewed|read|analyzed)|"
    r"your\s+(?:manuscript|file|document|attachment)\s+(?:says|contains|shows|indicates|"
    r"mentions|describes|states)|"
    r"i\s+found\s+(?:in\s+)?(?:your\s+)?(?:manuscript|file|document|attachment)|"
    r"based\s+on\s+(?:your\s+)?(?:manuscript|file|document|attachment))\b",
    re.IGNORECASE,
)


def _attachment_content_review_claims(text: str, context_pack: ContextPack | None) -> list[str]:
    """Return review-claim phrases when attachments are present and claims are found."""
    if context_pack is None:
        return []
    if not context_pack.attachments_received:
        return []
    hits: list[str] = []
    for m in _ATTACHMENT_REVIEW_CLAIM_RE.finditer(text):
        hits.append(m.group(0))
    return hits


def _delegated_slot_reasks(text: str, context_pack: ContextPack | None) -> list[str]:
    """Return slot labels whose question forms appear in text after user delegated/declined them."""
    if context_pack is None:
        return []
    all_resolved = (
        list(context_pack.declined_slots or [])
        + list(context_pack.delegated_slots or [])
        + list(context_pack.unknown_slots or [])
    )
    if not all_resolved:
        return []
    violated: list[str] = []
    for status in all_resolved:
        if not status.forbidden_reask:
            continue
        pattern = _DELEGATED_SLOT_REASK_PATTERNS.get(status.slot)
        if pattern is not None and pattern.search(text):
            violated.append(status.slot)
    return violated


# ---------------------------------------------------------------------------
# Consultation-first quality checks (PR 2)
# ---------------------------------------------------------------------------

# Contact-capture-only phrases — response is only asking for contact with no answer.
_CONTACT_ONLY_RE = re.compile(
    r"\b(?:best\s+(?:name|email|number)|name\s+and\s+(?:email|phone|number)|"
    r"email\s+(?:address\s+)?(?:and|or)\s+(?:phone|number)|"
    r"(?:your\s+)?contact\s+(?:info(?:rmation)?|details?)|reach\s+you)\b",
    re.IGNORECASE,
)

# Scoping questions that should not appear after contact is captured.
_SCOPING_AFTER_CONTACT_RE = re.compile(
    r"\b(?:what\s+(?:genre|type\s+of\s+book)|which\s+genre|"
    r"word\s+count|page\s+count|how\s+many\s+(?:words|pages)|"
    r"manuscript\s+stage|what\s+stage|deadline|launch\s+(?:date|window))\b",
    re.IGNORECASE,
)


def _contact_only_when_priority_active(text: str, response_plan: ResponsePlan | None) -> bool:
    """Fail when primary goal is answer_current_question but response only asks for contact."""
    if response_plan is None:
        return False
    if response_plan.primary_goal != "answer_current_question":
        return False
    # Response must contain some answer, not just a contact request.
    has_contact_ask = bool(_CONTACT_ONLY_RE.search(text))
    has_question_mark = "?" in text
    # If the only question is a contact ask, that's a violation.
    if has_contact_ask and has_question_mark:
        # Allow if there is substantive content before the contact ask.
        # Heuristic: response should be at least 60 chars long and have > 1 sentence.
        stripped = text.strip()
        sentences = [s.strip() for s in stripped.split(".") if s.strip()]
        if len(stripped) < 60 or len(sentences) <= 1:
            return True
    return False


def _wrong_scoping_after_contact_ready(text: str, context_pack: ContextPack | None) -> bool:
    """Fail when contact is ready but response asks genre/word_count/deadline."""
    if context_pack is None:
        return False
    if context_pack.contact_capture_status != "ready":
        return False
    if context_pack.preferred_call_time:
        return False  # already have call time
    return bool(_SCOPING_AFTER_CONTACT_RE.search(text))


# ---------------------------------------------------------------------------
# Attachment priority quality checks (PR 3)
# ---------------------------------------------------------------------------

_ATTACHMENT_MANUSCRIPT_STAGE_RE = re.compile(
    r"\b(?:what\s+stage|manuscript\s+stage|starting\s+from\s+scratch|"
    r"have\s+(?:a\s+)?draft|written\s+anything|how\s+far\s+along|"
    r"completed\s+draft|partial\s+draft|how\s+much\s+(?:have\s+you|is)\s+written|"
    r"is\s+(?:it\s+)?(?:complete|finished|done)\?)\b",
    re.IGNORECASE,
)

_ATTACHMENT_WORD_COUNT_RE = re.compile(
    r"\b(?:word\s+count|page\s+count|how\s+many\s+(?:words|pages)|"
    r"word\s+or\s+page|how\s+long\s+(?:is\s+)?(?:it|your\s+manuscript|the\s+book))\b",
    re.IGNORECASE,
)


def _attachment_asks_manuscript_stage(text: str, context_pack: ContextPack | None) -> bool:
    """Fail when the response asks draft/manuscript stage on an attachment turn."""
    if context_pack is None or not context_pack.attachments_received:
        return False
    return bool(_ATTACHMENT_MANUSCRIPT_STAGE_RE.search(text))


def _attachment_asks_word_count(text: str, context_pack: ContextPack | None) -> bool:
    """Fail when the response asks word/page count on an attachment turn."""
    if context_pack is None or not context_pack.attachments_received:
        return False
    return bool(_ATTACHMENT_WORD_COUNT_RE.search(text))


# ---------------------------------------------------------------------------
# Repair / fallback helpers
# ---------------------------------------------------------------------------


def _build_repair_instructions(failures: list[str], tone_suggestions: list[str]) -> str:
    parts: list[str] = ["Rewrite to fix:"]
    for f in failures:
        if "internal_artifact" in f:
            parts.append("- Remove all internal implementation terms.")
        elif "known_fact_reask" in f:
            parts.append("- Do not ask for facts already shared.")
        elif "wrong_service" in f:
            parts.append("- Do not mention unrelated services.")
        elif "too_many_questions" in f:
            parts.append("- Ask exactly one question.")
        elif "unapproved_price" in f:
            parts.append("- Do not quote prices without an approved estimate.")
        elif "timeline" in f:
            parts.append("- Do not promise specific delivery timelines.")
        elif "markdown" in f:
            parts.append("- Use plain prose; remove markdown formatting.")
        elif "weak_language" in f:
            parts.append("- Remove repeated hedging words.")
        elif "missing_next_step" in f:
            parts.append("- End with one clear question or next step.")
        elif "blocked_action" in f:
            parts.append("- Do not claim the blocked action completed.")
        elif "assumption_leak" in f:
            parts.append(
                "- Do not assert genre/category as confirmed when the user has not confirmed it."
            )
        elif "greeting_asked_scoping" in f:
            parts.append("- This is a greeting turn. Welcome warmly; do not ask scoping questions.")
        elif "contact_only_without_answering" in f:
            parts.append(
                "- Answer the user's current question first, then offer consultation. "
                "Do not open with a contact request."
            )
        elif "scoping_question_after_contact_ready" in f:
            parts.append(
                "- Contact is already captured. Ask for preferred call time, "
                "not genre or word count."
            )
        elif "attachment_turn_asks_manuscript_stage" in f:
            parts.append(
                "- An attachment was received. Do not ask about manuscript stage or draft status. "
                "Acknowledge receipt and route to specialist assessment."
            )
        elif "attachment_turn_asks_word_count" in f:
            parts.append(
                "- An attachment was received. Do not ask for word count or page count. "
                "Route to specialist assessment instead."
            )
        elif "sales_tone" in f:
            parts.append(
                "- Rewrite in warm, specific, consultative tone with one clear next question."
            )
    if tone_suggestions:
        parts.append("Tone guidance:")
        for suggestion in tone_suggestions[:3]:
            parts.append(f"- {suggestion}")
    return " ".join(parts)


def _build_safe_fallback(
    *,
    failures: list[str],
    context_pack: ContextPack | None,
    response_plan: ResponsePlan | None,
    tool_governance: ToolGovernanceDecision | None,
    intent: IntentVote,
) -> str:
    del failures  # failures inform repair_instructions; fallback uses plan/context

    # Prefer governance blocked_message when that is the trigger.
    if tool_governance is not None and not tool_governance.allowed:
        if tool_governance.blocked_message:
            msg = tool_governance.blocked_message
            if not _contains_internal(msg):
                return msg

    # Use plan's customer_safe_tool_summary when available.
    if response_plan is not None and response_plan.customer_safe_tool_summary:
        base = response_plan.customer_safe_tool_summary
        if not _contains_internal(base):
            if response_plan.next_question:
                return f"{base} {_fact_key_to_question(response_plan.next_question)}"
            return base

    # Assemble from active service + next question.
    parts: list[str] = []
    service = context_pack.active_service if context_pack else None
    if service:
        parts.append(f"I can help with {_human_service_name(service)}.")
    if context_pack and context_pack.forbidden_reasks:
        parts.append("I have the details you shared.")
    if response_plan and response_plan.next_question:
        parts.append(_fact_key_to_question(response_plan.next_question))
    elif intent.query_primary in _PRICING_INTENTS:
        parts.append(
            "What word count or page count, genre, manuscript stage, and deadline should I use?"
        )
    else:
        parts.append("What would you like to focus on next?")

    return " ".join(parts) if parts else "I'd be happy to help — what should we focus on?"


def _contains_internal(text: str) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in _INTERNAL_WORDS)


def _build_style_safe_fallback(
    *,
    context_pack: ContextPack | None,
    response_plan: ResponsePlan | None,
    intent: IntentVote,
) -> str:
    service = context_pack.active_service if context_pack is not None else None
    genre = context_pack.active_genre if context_pack is not None else None
    manuscript = context_pack.manuscript_status if context_pack is not None else None

    detail_parts: list[str] = []
    if genre:
        detail_parts.append(genre)
    if manuscript:
        detail_parts.append(manuscript.replace("_", " "))

    if service:
        service_text = _human_service_name(service)
        if detail_parts:
            opener = f"Based on what you shared about {service_text} and {', '.join(detail_parts)},"
        else:
            opener = f"Based on what you shared about {service_text},"
    else:
        opener = (
            f"Based on what you shared about {', '.join(detail_parts)},"
            if detail_parts
            else "Based on what you shared,"
        )

    if response_plan is not None and response_plan.next_question:
        return (
            f"{opener} the useful next step is to confirm one detail. "
            f"{_fact_key_to_question(response_plan.next_question)}"
        )

    if intent.query_primary in _PRICING_INTENTS:
        return (
            f"{opener} we can move this forward once scope is clear. "
            "What word count or page count should I use?"
        )

    return f"{opener} we can move this forward with one detail. What should we focus on next?"


def _fact_key_to_question(key: str) -> str:
    questions: dict[str, str] = {
        "cover_style": "What cover style or visual direction should I use?",
        "word_or_page_count": "What rough word count or page count should I use?",
        "genre": "What genre or book category should I use?",
        "genre_options": (
            "Which best describes your book: fiction, memoir/personal story, "
            "business/self-help, children's book, or not sure yet?"
        ),
        "manuscript_stage": "What stage is the manuscript in?",
        "manuscript_stage_options": (
            "Where is your manuscript right now: just an idea, rough notes, outline, "
            "partial draft, full draft, or completed manuscript?"
        ),
        "service_options": (
            "Which of these are you looking for help with: writing, editing, cover design, "
            "formatting, publishing, marketing, or not sure yet?"
        ),
        "deadline": "What deadline or launch window should I plan for?",
        "services": "Which services would you like help with?",
        "how_can_we_help": "What can I help you with today?",
        # Consultation-first questions (PR 2).
        "preferred_call_time": (
            "What's the best time to reach you — morning, afternoon, or evening?"
        ),
        "consultation_interest": (
            "Would you like to connect with a BookCraft specialist for a free consultation?"
        ),
        "name_and_email_or_phone": (
            "What's the best name and email address or phone number to reach you?"
        ),
        # Flexible intent routing questions.
        "manuscript_stage_or_project_status": (
            "What stage is your manuscript in, and what are you hoping to achieve?"
        ),
        "portfolio_filter": "Which service or genre would you like samples for?",
        "same_or_new_project": "Is this the same book we were discussing, or a new project?",
    }
    return questions.get(key, f"Could you share more about {key.replace('_', ' ')}?")


def _human_service_name(service: str) -> str:
    names: dict[str, str] = {
        "ghostwriting": "ghostwriting",
        "editing_proofreading": "editing and proofreading",
        "cover_design_illustration": "cover design and illustration",
        "interior_formatting": "interior formatting",
        "publishing_distribution": "publishing and distribution",
        "marketing_promotion": "marketing and promotion",
        "audiobook_production": "audiobook production",
        "author_website": "author website design",
        "video_trailer": "video trailer production",
    }
    return names.get(service, service.replace("_", " "))


# Suppress unused-import warning — Any is used in the module-level type context.
_: Any = None
del _
