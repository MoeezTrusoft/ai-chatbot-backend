"""AnswerBeforeCapturePolicy.

Produces structured guidance for the response planner when the user has asked
a direct question that must be answered before contact capture.

Engines compute structured guidance. Claude writes final customer-facing text.
No hardcoded customer prose is produced here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.sales.current_question_priority import CurrentQuestionPriorityResult

# ---------------------------------------------------------------------------
# Answer-focus and boundary tables
# ---------------------------------------------------------------------------

# Maps question_type -> answer_focus hint passed to Claude via response plan.
_ANSWER_FOCUS: dict[str, str] = {
    "pricing": "pricing_explanation_scope_based",
    "rough_estimate": "rough_pricing_range_factors",
    "timeline": "timeline_explanation_scope_based",
    "samples": "portfolio_and_samples_intro",
    "distribution": "distribution_platform_support_explanation",
    "christian_publishing": "faith_based_manuscript_support",
    "fiverr_comparison": "professional_managed_service_positioning",
    "free_sample": "sample_policy_and_consultation_offer",
    "process": "service_process_overview",
    "service_advice": "service_recommendation_guidance",
    "guarantee_or_sales_claim": "quality_commitment_honest_positioning",
    "contact_refusal": "respect_refusal_answer_concern_offer_later",
    "topic_correction": "acknowledge_correction_answer_corrected_topic",
}

# Maps question_type -> safety boundary passed to Claude.
_BOUNDARY: dict[str, str] = {
    "pricing": "no_invented_price_figures; explain scope factors only",
    "rough_estimate": "no_invented_numbers; give factor-based range logic",
    "timeline": "no_committed_timeline_without_approved_quote",
    "samples": "no_fake_links; direct to portfolio or consultation",
    "distribution": "no_fake_publisher_deals; explain platform support safely",
    "christian_publishing": (
        "no_claimed_publisher_relationships_unless_confirmed; "
        "explain faith-based manuscript and market positioning support"
    ),
    "fiverr_comparison": "no_competitor_attacks; honest professional service positioning only",
    "free_sample": "no_free_work_promises; explain sample/consultation options safely",
    "process": "no_committed_timelines_or_prices; overview only",
    "service_advice": "no_invented_services; use confirmed BookCraft service list",
    "guarantee_or_sales_claim": (
        "no_guarantees_of_bestseller_or_specific_outcomes; "
        "quality commitment and specialist review only"
    ),
    "contact_refusal": "do_not_ask_contact_again; answer concern then offer consultation",
    "topic_correction": (
        "acknowledge_mistake_clearly; answer the corrected topic; "
        "do_not_return_to_previous_wrong_topic"
    ),
}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class AnswerBeforeCaptureDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_answer_first: bool = False
    answer_focus: str | None = None
    boundary: str | None = None
    consultation_bridge: bool = False
    suppress_contact_until_answered: bool = False
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class AnswerBeforeCapturePolicy:
    """
    Decides whether and how to answer a direct question before capturing contact.

    Returns structured guidance for the response planner; Claude generates
    the actual customer-facing answer within those constraints.
    """

    def decide(
        self,
        *,
        priority: CurrentQuestionPriorityResult,
        contact_ready: bool = False,
    ) -> AnswerBeforeCaptureDecision:
        audit: list[str] = []

        if not priority.has_priority:
            audit.append("no_priority:pass_through")
            return AnswerBeforeCaptureDecision(audit=audit)

        qt = priority.question_type or "unknown"
        audit.append(f"question_type:{qt}")

        # When contact is already captured there is no capture to suppress.
        suppress_contact = not contact_ready and priority.should_answer_before_capture
        if suppress_contact:
            audit.append("suppress_contact_until_answered")

        # Bridge to consultation for most question types (not contact_refusal).
        bridge = qt not in {"contact_refusal", "topic_correction"}
        if bridge:
            audit.append("consultation_bridge:yes")

        return AnswerBeforeCaptureDecision(
            should_answer_first=True,
            answer_focus=_ANSWER_FOCUS.get(qt),
            boundary=_BOUNDARY.get(qt),
            consultation_bridge=bridge,
            suppress_contact_until_answered=suppress_contact,
            audit=audit,
        )
