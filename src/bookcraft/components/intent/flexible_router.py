from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.intent.schemas import IntentVote

# ---------------------------------------------------------------------------
# Detection patterns — reusable groups, not one-off per-case checks
# ---------------------------------------------------------------------------

_CONSULTATION_RE = re.compile(
    r"\b(?:talk\s+to\s+someone|speak\s+with\s+someone|schedule\s+a\s+call"
    r"|consultation|discuss\s+with\s+your\s+team|expert\s+can\s+guide\s+me"
    r"|team\s+can\s+guide\s+me)\b",
    re.IGNORECASE,
)

_DISCRETION_RE = re.compile(
    r"\b(?:you\s+decide|you\s+suggest|your\s+choice|bookcraft\s+can\s+decide"
    r"|i\s+leave\s+it\s+to\s+bookcraft|i\s+trust\s+your\s+team"
    r"|whatever\s+you\s+think\s+is\s+best|whatever\s+is\s+best"
    r"|use\s+your\s+own\s+creativity|come\s+up\s+with\s+your\s+own"
    r"|you\s+guys\s+decide)\b",
    re.IGNORECASE,
)

_SERVICE_GUIDANCE_RE = re.compile(
    r"\b(?:i\s+don'?t\s+know\s+what\s+i\s+need|i\s+do\s+not\s+know\s+what\s+i\s+need"
    r"|not\s+sure\s+what\s+service|not\s+sure\s+which\s+service"
    r"|don'?t\s+know\s+where\s+to\s+start|where\s+should\s+i\s+start"
    r"|can\s+you\s+guide\s+me|guide\s+me|what\s+do\s+you\s+recommend"
    r"|recommend\s+what\s+i\s+need|help\s+me\s+choose"
    r"|which\s+service\s+do\s+i\s+need)\b",
    re.IGNORECASE,
)

_PROCESS_RE = re.compile(
    r"\b(?:how\s+does\s+it\s+work|what\s+is\s+the\s+process"
    r"|how\s+will\s+you\s+handle\s+it|what\s+happens\s+next"
    r"|how\s+do\s+we\s+proceed|what\s+are\s+the\s+steps)\b",
    re.IGNORECASE,
)

# Portfolio fallback strategies that block flexible-router override.
_PORTFOLIO_FALLBACK_STRATEGIES = frozenset({"fallback_general_samples", "fallback_service_samples"})


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class FlexibleIntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected: bool
    mode: Literal[
        "service_guidance",
        "bookcraft_discretion",
        "consultation_handoff",
        "process_explanation",
        "not_flexible",
    ]
    recommended_primary_goal: str
    next_question: str | None = None
    confidence: float = 1.0
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class FlexibleIntentRouter:
    """Routes flexible / discretion user intent to the correct conversation mode."""

    def route(
        self,
        *,
        text: str,
        intent: IntentVote,
        state: Any,  # ThreadState — avoids circular import
        context_pack: Any | None = None,
        response_plan: Any | None = None,
        delegated_decision: Any | None = None,
        portfolio_fallback_decision: Any | None = None,
    ) -> FlexibleIntentDecision:
        del state, response_plan  # surfaced via context_pack

        audit: list[str] = []

        # Rule 6: Active portfolio fallback → do not override with service guidance.
        if portfolio_fallback_decision is not None:
            strategy = getattr(portfolio_fallback_decision, "strategy", None)
            if strategy in _PORTFOLIO_FALLBACK_STRATEGIES:
                audit.append("flexible:portfolio_fallback_active:skip")
                return FlexibleIntentDecision(
                    detected=False,
                    mode="not_flexible",
                    recommended_primary_goal="portfolio_matching",
                    confidence=0.0,
                    audit=audit,
                )

        # Signal detection.
        has_consultation = bool(_CONSULTATION_RE.search(text))
        has_discretion = bool(_DISCRETION_RE.search(text))
        has_guidance = bool(_SERVICE_GUIDANCE_RE.search(text))
        has_process = bool(_PROCESS_RE.search(text))

        delegated_status = (
            getattr(delegated_decision, "status", None) if delegated_decision else None
        )
        has_delegation = delegated_status == "delegated"

        if has_consultation:
            audit.append("flexible:consultation_cue_detected")
        if has_discretion:
            audit.append("flexible:discretion_cue_detected")
        if has_guidance:
            audit.append("flexible:guidance_cue_detected")
        if has_process:
            audit.append("flexible:process_cue_detected")
        if has_delegation:
            audit.append("flexible:delegated_decision_active")

        # Rule 1: Consultation takes highest priority.
        if has_consultation:
            return FlexibleIntentDecision(
                detected=True,
                mode="consultation_handoff",
                recommended_primary_goal="consultation_handoff",
                next_question="consultation_interest",
                confidence=0.92,
                audit=audit,
            )

        # Rule 3: BookCraft discretion — also elevated by existing delegation.
        if has_discretion or has_delegation:
            active_service = getattr(context_pack, "active_service", None) if context_pack else None
            if has_process or active_service:
                # Lean toward explanation when service context or process cue present.
                return FlexibleIntentDecision(
                    detected=True,
                    mode="process_explanation",
                    recommended_primary_goal="process_explanation",
                    next_question="consultation_interest",
                    confidence=0.88,
                    audit=audit + ["flexible:discretion_with_context→process_explanation"],
                )
            return FlexibleIntentDecision(
                detected=True,
                mode="bookcraft_discretion",
                recommended_primary_goal="consultation_handoff",
                next_question="consultation_interest",
                confidence=0.90,
                audit=audit,
            )

        # Rule 2: Service guidance — only when no single concrete service is clearly active.
        if has_guidance:
            active_service = getattr(context_pack, "active_service", None) if context_pack else None
            if active_service:
                # Rule 8: active service + guidance cue → discretion/process, not generic guidance.
                return FlexibleIntentDecision(
                    detected=True,
                    mode="process_explanation",
                    recommended_primary_goal="process_explanation",
                    next_question="consultation_interest",
                    confidence=0.85,
                    audit=audit + ["flexible:guidance_with_active_service→process_explanation"],
                )
            return FlexibleIntentDecision(
                detected=True,
                mode="service_guidance",
                recommended_primary_goal="flexible_service_guidance",
                next_question="manuscript_stage_or_project_status",
                confidence=0.88,
                audit=audit,
            )

        # Rule 4: Process explanation cue.
        if has_process:
            return FlexibleIntentDecision(
                detected=True,
                mode="process_explanation",
                recommended_primary_goal="process_explanation",
                next_question="consultation_interest",
                confidence=0.85,
                audit=audit,
            )

        return FlexibleIntentDecision(
            detected=False,
            mode="not_flexible",
            recommended_primary_goal="continue_discovery",
            confidence=0.0,
            audit=audit + ["flexible:not_detected"],
        )
