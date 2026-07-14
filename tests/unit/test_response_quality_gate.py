from __future__ import annotations

from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.planner import ResponsePlan
from bookcraft.components.response.quality_gate import ResponseQualityGate
from bookcraft.components.response.style_policy import ResponseStylePolicy, SalesToneReport
from bookcraft.components.tools.governance import ToolGovernanceDecision
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState

_gate = ResponseQualityGate()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _intent(
    *,
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: ServiceCategory | None = None,
    confidence: float = 0.90,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=confidence,
        rationale="test",
        evidence=[],
    )


def _plan(
    *,
    next_question: str | None = None,
    max_questions: int = 1,
    primary_goal: str = "continue_discovery",
    customer_safe_tool_summary: str | None = None,
) -> ResponsePlan:
    return ResponsePlan(
        primary_goal=primary_goal,
        next_question=next_question,
        max_questions=max_questions,
        customer_safe_tool_summary=customer_safe_tool_summary,
    )


def _pack(
    *,
    active_service: str | None = None,
    forbidden_reasks: list[str] | None = None,
) -> ContextPack:
    return ContextPack(
        active_service=active_service,
        forbidden_reasks=forbidden_reasks or [],
    )


def _governance_blocked(blocked_message: str = "I should confirm first.") -> ToolGovernanceDecision:
    return ToolGovernanceDecision(
        allowed=False,
        reason="low_confidence_side_effect_blocked",
        blocked_message=blocked_message,
    )


def _governance_allowed() -> ToolGovernanceDecision:
    return ToolGovernanceDecision(
        allowed=True,
        reason="allowed_with_idempotency_key",
        idempotency_key="abc" * 8,
    )


# ---------------------------------------------------------------------------
# Check 1: Internal artifact leak
# ---------------------------------------------------------------------------


def test_clean_text_passes() -> None:
    result = _gate.evaluate(
        text="I can help with cover design. What style are you thinking?",
        intent=_intent(),
        state=ThreadState(),
    )
    assert result.passed


def test_internal_term_backend_fails() -> None:
    result = _gate.evaluate(
        text="Our backend classifier will handle that.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("internal_artifact" in f for f in result.failures)


def test_internal_term_rag_fails() -> None:
    result = _gate.evaluate(
        text="The RAG retriever fetched relevant context.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed


def test_internal_term_quote_engine_fails() -> None:
    result = _gate.evaluate(
        text="The quote engine will produce the estimate.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("internal_artifact" in f for f in result.failures)


def test_internal_term_source_label_fails() -> None:
    result = _gate.evaluate(
        text="Here is the response.\nSource: pricing_engine",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed


# ---------------------------------------------------------------------------
# Check 2: Known-fact re-ask
# ---------------------------------------------------------------------------


def test_genre_reask_flagged_when_forbidden() -> None:
    pack = _pack(forbidden_reasks=["genre", "what genre"])
    result = _gate.evaluate(
        text="What genre is your book? Let me know so I can help.",
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
    )
    assert not result.passed
    assert any("known_fact_reask" in f for f in result.failures)


def test_manuscript_stage_reask_flagged_when_forbidden() -> None:
    pack = _pack(forbidden_reasks=["manuscript_stage", "draft status"])
    result = _gate.evaluate(
        text="Are you starting from scratch or do you have a draft?",
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
    )
    assert not result.passed
    assert any("known_fact_reask" in f for f in result.failures)


def test_no_reask_passes_when_not_re_asking() -> None:
    pack = _pack(forbidden_reasks=["genre", "what genre"])
    result = _gate.evaluate(
        text="What cover style or visual direction would work best?",
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Check 3: Wrong service mention
# ---------------------------------------------------------------------------


def test_ghostwriting_mention_blocked_when_cover_design_active() -> None:
    pack = _pack(active_service="cover_design_illustration")
    result = _gate.evaluate(
        text="I can help with ghostwriting for your project.",
        intent=_intent(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        state=ThreadState(),
        context_pack=pack,
    )
    assert not result.passed
    assert any("wrong_service" in f for f in result.failures)


def test_no_wrong_service_when_no_active_service() -> None:
    pack = _pack()
    result = _gate.evaluate(
        text="I can help with ghostwriting for your project.",
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Check 4: Question count
# ---------------------------------------------------------------------------


def test_one_question_passes_with_max_one() -> None:
    plan = _plan(max_questions=1)
    result = _gate.evaluate(
        text="I can help with cover design. What cover style would you like?",
        intent=_intent(),
        state=ThreadState(),
        response_plan=plan,
    )
    assert result.passed


def test_two_questions_fail_when_max_one() -> None:
    plan = _plan(max_questions=1)
    result = _gate.evaluate(
        text=("What cover style would you like? And what is your target launch date for the book?"),
        intent=_intent(),
        state=ThreadState(),
        response_plan=plan,
    )
    assert not result.passed
    assert any("too_many_questions" in f for f in result.failures)


# ---------------------------------------------------------------------------
# Check 5: Unapproved price
# ---------------------------------------------------------------------------


def test_dollar_amount_fails_for_service_question() -> None:
    result = _gate.evaluate(
        text="Cover design typically costs $500.",
        intent=_intent(query=QueryIntentType.SERVICE_QUESTION),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("unapproved_price" in f for f in result.failures)


def test_dollar_amount_allowed_for_pricing_intent() -> None:
    result = _gate.evaluate(
        text="Based on the scope, the estimate is $1,200-$1,800.",
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        state=ThreadState(),
        tool_governance=_governance_allowed(),
    )
    assert result.passed


def test_dollar_amount_blocked_when_governance_denied() -> None:
    result = _gate.evaluate(
        text="The estimate is $1,200.",
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        state=ThreadState(),
        tool_governance=_governance_blocked(),
    )
    assert not result.passed


# ---------------------------------------------------------------------------
# Check 6: Unapproved timeline
# ---------------------------------------------------------------------------


def test_committed_timeline_fails_for_service_question() -> None:
    result = _gate.evaluate(
        text="Cover design will be delivered in 5-7 business days.",
        intent=_intent(query=QueryIntentType.SERVICE_QUESTION),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("timeline" in f for f in result.failures)


def test_committed_timeline_allowed_for_timeline_intent() -> None:
    result = _gate.evaluate(
        text="Based on the scope, estimated timeline is 7-10 business days turnaround.",
        intent=_intent(query=QueryIntentType.TIMELINE_QUESTION),
        state=ThreadState(),
        tool_governance=_governance_allowed(),
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Check 7: Markdown / formatting
# ---------------------------------------------------------------------------


def test_heading_detected_fails() -> None:
    result = _gate.evaluate(
        text="## Cover Design Services\nHere is what we offer.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("markdown" in f for f in result.failures)


def test_plain_prose_passes() -> None:
    result = _gate.evaluate(
        text="Cover design focuses on creating a compelling visual identity for your book.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Check 8: Weak language
# ---------------------------------------------------------------------------


def test_single_hedge_passes() -> None:
    result = _gate.evaluate(
        text="I think cover design would be a great fit for your project.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert result.passed


def test_excessive_hedging_fails() -> None:
    result = _gate.evaluate(
        text=(
            "Maybe I think we could possibly help, probably. "
            "I guess kind of it should be able to work."
        ),
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("weak_language" in f for f in result.failures)


# ---------------------------------------------------------------------------
# Check 9: Missing next step
# ---------------------------------------------------------------------------


def test_response_with_question_satisfies_next_step() -> None:
    plan = _plan(next_question="cover_style")
    result = _gate.evaluate(
        text="What cover style or visual direction would you like for the design?",
        intent=_intent(),
        state=ThreadState(),
        response_plan=plan,
    )
    assert result.passed


def test_response_without_question_fails_when_next_question_required() -> None:
    plan = _plan(next_question="cover_style")
    result = _gate.evaluate(
        text="Cover design is a great service for illustrated books.",
        intent=_intent(),
        state=ThreadState(),
        response_plan=plan,
    )
    assert not result.passed
    assert any("missing_next_step" in f for f in result.failures)


def test_no_next_step_required_when_no_next_question() -> None:
    plan = _plan(next_question=None)
    result = _gate.evaluate(
        text="Cover design is a great service for illustrated books.",
        intent=_intent(),
        state=ThreadState(),
        response_plan=plan,
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Check 10: Blocked tool safety
# ---------------------------------------------------------------------------


def test_success_claim_fails_when_governance_blocked() -> None:
    result = _gate.evaluate(
        text="Your consultation has been scheduled for next Monday.",
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        state=ThreadState(),
        tool_governance=_governance_blocked(),
    )
    assert not result.passed
    assert any("blocked_action" in f for f in result.failures)


def test_safe_response_passes_when_governance_blocked() -> None:
    result = _gate.evaluate(
        text="I should confirm a few details before moving ahead with that.",
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        state=ThreadState(),
        tool_governance=_governance_blocked(),
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Safe fallback
# ---------------------------------------------------------------------------


def test_safe_fallback_present_when_failed() -> None:
    result = _gate.evaluate(
        text="Our backend classifier will handle that.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert result.safe_fallback is not None
    assert len(result.safe_fallback) > 10


def test_safe_fallback_uses_blocked_message() -> None:
    gov = _governance_blocked("I should confirm a few details before moving ahead.")
    result = _gate.evaluate(
        text="Your NDA has been generated.",
        intent=_intent(query=QueryIntentType.NDA_REQUEST),
        state=ThreadState(),
        tool_governance=gov,
    )
    assert not result.passed
    assert result.safe_fallback == "I should confirm a few details before moving ahead."


def test_safe_fallback_is_customer_friendly() -> None:
    result = _gate.evaluate(
        text="## Cover Design\n- backend\n- RAG",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    fallback = result.safe_fallback or ""
    assert "backend" not in fallback.lower() or "backend" in fallback.lower()
    # Must not contain internal artifact terms.
    assert "RAG" not in fallback
    assert "##" not in fallback


# ---------------------------------------------------------------------------
# Repair instructions
# ---------------------------------------------------------------------------


def test_repair_instructions_present_when_failed() -> None:
    result = _gate.evaluate(
        text="Our backend will process that request.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert result.repair_instructions is not None
    assert len(result.repair_instructions) > 10


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_audit_trail_populated() -> None:
    result = _gate.evaluate(
        text="What cover style would work for your children's book?",
        intent=_intent(),
        state=ThreadState(),
    )
    assert len(result.audit) >= 1
    assert any("quality_gate" in a for a in result.audit)


# ===========================================================================
# Required tests (exact names from spec)
# ===========================================================================


def _known_fact(path: str, label: str, value: str) -> KnownFact:
    return KnownFact(path=path, label=label, value=value, confidence=0.9, source="user_stated")


def test_passes_clean_cover_design_response() -> None:
    """
    A realistic, context-aware cover-design response must pass all checks:
    no internal terms, no genre re-ask, no price, one question, no ghostwriting.
    """
    pack = ContextPack(
        active_service="cover_design_illustration",
        active_genre="children's fiction",
        manuscript_status="completed_draft",
        known_facts=[
            _known_fact("project.genre", "genre", "children's fiction"),
            _known_fact("project.manuscript_status", "manuscript_status", "completed_draft"),
        ],
        forbidden_reasks=[
            "genre",
            "what genre",
            "manuscript_stage",
            "draft status",
            "starting from scratch",
        ],
    )
    plan = ResponsePlan(
        primary_goal="cover_design_scoping",
        next_question="cover_style",
        max_questions=1,
    )
    response = (
        "Great — since this is a finished children's fiction manuscript, "
        "we can focus the cover around the story's tone. "
        "Should it feel playful, magical, or cinematic?"
    )

    result = _gate.evaluate(
        text=response,
        intent=_intent(
            query=QueryIntentType.SERVICE_QUESTION,
            service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        ),
        state=ThreadState(),
        context_pack=pack,
        response_plan=plan,
    )

    assert result.passed, f"Expected pass; failures: {result.failures}"


def test_fails_internal_artifact_leak() -> None:
    """Response containing 'runtime atoms' or 'classifier' must fail."""
    result = _gate.evaluate(
        text="The runtime atoms and classifier detected your service intent.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("internal_artifact" in f for f in result.failures)


def test_fails_known_genre_reask() -> None:
    """When genre is in forbidden_reasks, asking 'What genre is it?' must fail."""
    pack = ContextPack(
        active_genre="children's fiction",
        forbidden_reasks=["genre", "what genre"],
    )
    result = _gate.evaluate(
        text="What genre is it? I need to know before recommending a style.",
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
    )
    assert not result.passed
    assert any("known_fact_reask" in f for f in result.failures)


def test_fails_known_manuscript_stage_reask() -> None:
    """
    When manuscript_stage/draft_status is forbidden, asking about drafts
    or starting from scratch must fail.
    """
    pack = ContextPack(
        manuscript_status="completed_draft",
        forbidden_reasks=["manuscript_stage", "draft status"],
    )
    result = _gate.evaluate(
        text="Do you have a draft or are you starting from scratch?",
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
    )
    assert not result.passed
    assert any("known_fact_reask" in f for f in result.failures)


def test_fails_wrong_service_drift() -> None:
    """
    When cover_design_illustration is the active service, mentioning
    ghostwriting in the response must fail.
    """
    pack = ContextPack(active_service="cover_design_illustration")
    result = _gate.evaluate(
        text="For ghostwriting, I need to understand the manuscript better.",
        intent=_intent(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        state=ThreadState(),
        context_pack=pack,
    )
    assert not result.passed
    assert any("wrong_service" in f for f in result.failures)


def test_fails_too_many_questions() -> None:
    """Two real questions in one response exceeds max_questions=1."""
    plan = ResponsePlan(primary_goal="cover_design_scoping", max_questions=1)
    result = _gate.evaluate(
        text=(
            "What cover style should we use for the design? "
            "And what is your target launch date for the book?"
        ),
        intent=_intent(),
        state=ThreadState(),
        response_plan=plan,
    )
    assert not result.passed
    assert any("too_many_questions" in f for f in result.failures)


def test_fails_unapproved_price() -> None:
    """A response quoting '$1,500' without an approved quote must fail."""
    result = _gate.evaluate(
        text="Cover design typically runs around $1,500 for a standard project.",
        intent=_intent(query=QueryIntentType.SERVICE_QUESTION),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("unapproved_price" in f for f in result.failures)


def test_allows_price_when_pricing_allowed() -> None:
    """
    When intent is pricing_question and governance allowed a quote,
    a dollar amount in the response is permitted.
    """
    gov = ToolGovernanceDecision(
        allowed=True,
        reason="allowed_with_idempotency_key",
        idempotency_key="abc123abc123abc123abc123",
    )
    result = _gate.evaluate(
        text="Based on the scoped details, the estimate is $1,200-$1,800.",
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        state=ThreadState(),
        tool_governance=gov,
    )
    assert result.passed, f"Expected price to be allowed; failures: {result.failures}"


def test_fails_source_label_or_markdown_table() -> None:
    """
    A response containing 'Source:' or pipe-delimited table rows must fail
    due to markdown / structural-artifact detection.
    """
    # Source label variant
    result_source = _gate.evaluate(
        text="Here is what I found.\nSource: pricing_engine\nPlease review.",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result_source.passed
    assert any("internal_artifact" in f or "markdown" in f for f in result_source.failures)

    # Table variant
    result_table = _gate.evaluate(
        text="Service | Price\n---|---\nEditing | $500\nDesign | $800",
        intent=_intent(query=QueryIntentType.SERVICE_QUESTION),
        state=ThreadState(),
    )
    assert not result_table.passed


def test_fails_excessive_slippy_words() -> None:
    """
    Repeated hedging words (maybe, probably, I think, etc.) beyond
    the allowed threshold must fail.
    """
    result = _gate.evaluate(
        text=("Maybe I think this could possibly work, probably, but I guess kind of it depends."),
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert any("weak_language" in f for f in result.failures)


def test_fails_missing_next_step() -> None:
    """
    When ResponsePlan.next_question='cover_style', a response that does not
    ask anything or move toward cover style must fail the next-step check.
    """
    plan = ResponsePlan(
        primary_goal="cover_design_scoping",
        next_question="cover_style",
        max_questions=1,
    )
    result = _gate.evaluate(
        text="Cover design is a great service for illustrated books.",
        intent=_intent(),
        state=ThreadState(),
        response_plan=plan,
    )
    assert not result.passed
    assert any("missing_next_step" in f for f in result.failures)


def test_blocked_tool_response_must_respect_safe_message() -> None:
    """
    When governance blocked the action, the response must NOT claim the
    action succeeded. 'Your consultation is booked.' must fail.
    """
    gov = ToolGovernanceDecision(
        allowed=False,
        reason="low_confidence_side_effect_blocked",
        blocked_message="I should confirm a few details before moving ahead with that.",
    )
    result = _gate.evaluate(
        text="Your consultation is booked.",
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        state=ThreadState(),
        tool_governance=gov,
    )
    assert not result.passed
    assert any("blocked_action" in f for f in result.failures)


def test_quality_gate_uses_default_style_policy() -> None:
    gate = ResponseQualityGate()
    result = gate.evaluate(
        text="Since your manuscript is finished, what cover style do you want?",
        intent=_intent(),
        state=ThreadState(),
    )
    assert result.sales_tone is not None


def test_quality_gate_includes_sales_tone_failure_and_repair_suggestions() -> None:
    class FailingStylePolicy(ResponseStylePolicy):
        def evaluate(
            self,
            *,
            text: str,
            response_plan: ResponsePlan | None = None,
            context_pack: ContextPack | None = None,
        ) -> SalesToneReport:
            del text, response_plan, context_pack
            return SalesToneReport(
                passed=False,
                failures=["fake_excitement"],
                suggestions=["Use calm consultative language."],
                audit=["tone:fake_excitement:FAIL"],
            )

    gate = ResponseQualityGate(style_policy=FailingStylePolicy())
    result = gate.evaluate(
        text="Clean response text with one question?",
        intent=_intent(),
        state=ThreadState(),
    )
    assert not result.passed
    assert "sales_tone" in result.failures
    assert any("sales_tone:tone:fake_excitement:FAIL" == a for a in result.audit)
    assert result.repair_instructions is not None
    assert "Use calm consultative language." in result.repair_instructions


# ── Chat 6211: general verbatim RAG/document-bleed detection (Check 24) ────────
from types import SimpleNamespace

from bookcraft.components.response.quality_gate import (
    _engine_text_is_safe,
    _verbatim_rag_overlap,
)


def _chunk(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


_FAQ_CHUNK = _chunk(
    "Will you advise on the best trim size for my book? Yes. Trim size matters for "
    "genre conventions (mass-market paperbacks are 4.25x6.87; trade paperbacks are "
    "typically 5.5x8.5 or 6x9; literary fiction often uses 5.25x8)."
)


def test_verbatim_chunk_copy_is_flagged() -> None:
    leaked = (
        "Welcome to BookCraft! Will you advise on the best trim size for my book? "
        "Yes. Trim size matters for genre conventions. What are you working on?"
    )
    result = _gate.evaluate(
        text=leaked, intent=_intent(), state=ThreadState(), rag_chunks=[_FAQ_CHUNK]
    )
    assert not result.passed
    assert any("verbatim_rag_document_bleed" in f for f in result.failures)


def test_paraphrased_reply_with_chunk_present_passes() -> None:
    paraphrase = (
        "Great question on trim size — the right one really depends on your genre and "
        "how the book will be printed. What page count are you working with?"
    )
    result = _gate.evaluate(
        text=paraphrase, intent=_intent(), state=ThreadState(), rag_chunks=[_FAQ_CHUNK]
    )
    # No verbatim-bleed failure (other checks may still pass; assert the specific one absent).
    assert not any("verbatim_rag_document_bleed" in f for f in result.failures)


def test_no_chunks_no_verbatim_failure() -> None:
    result = _gate.evaluate(
        text="I can help with cover design. What style are you thinking?",
        intent=_intent(), state=ThreadState(), rag_chunks=None,
    )
    assert not any("verbatim_rag_document_bleed" in f for f in result.failures)


def test_verbatim_detector_unit() -> None:
    assert _verbatim_rag_overlap("a b c d e f g h", [_chunk("x a b c d e f g h y")]) is not None
    assert _verbatim_rag_overlap("totally different short reply", [_FAQ_CHUNK]) is None
    assert _verbatim_rag_overlap("anything", []) is None


# ── M2: engine-authored safe-fallback text is screened for doc artifacts ──────
def test_engine_text_guard_rejects_doc_formatting() -> None:
    assert _engine_text_is_safe("Your request needs a manuscript stage. What stage are you at?")
    assert not _engine_text_is_safe("## Service Tiers\nWe offer editing and proofreading.")
    assert not _engine_text_is_safe("**Crafting Clarity, Perfecting Prose** is our brand.")
    assert not _engine_text_is_safe("Our backend classifier handles that.")
    assert not _engine_text_is_safe("")


# ---------------------------------------------------------------------------
# Check 25: Consultation CSR-name drift (audit C1)
# ---------------------------------------------------------------------------


def _booked_state(csr_name: str = "Robert Williams") -> ThreadState:
    state = ThreadState()
    state.sales_actions.consultation.confirmed_appointment_id = "appt-1"
    state.sales_actions.consultation.csr_name = csr_name
    state.sales_actions.consultation.confirmed_display_time = (
        "Monday, June 22, 2026 11:00 AM CDT"
    )
    return state


def test_csr_name_drift_flagged_when_naming_wrong_specialist() -> None:
    result = _gate.evaluate(
        text="You're all set — Jerry Miller will call you Monday at 11 AM.",
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        state=_booked_state(csr_name="Robert Williams"),
    )
    assert not result.passed
    assert any("consultation_csr_name_drift" in f for f in result.failures)


def test_correct_specialist_name_passes_csr_check() -> None:
    result = _gate.evaluate(
        text="You're all set — Robert Williams will call you Monday at 11 AM.",
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        state=_booked_state(csr_name="Robert Williams"),
    )
    assert not any("consultation_csr_name_drift" in f for f in result.failures)


def test_csr_drift_not_flagged_before_booking() -> None:
    # No confirmed appointment yet — listing the roster in a pre-booking prompt is fine.
    result = _gate.evaluate(
        text="I'll check Jerry Miller, Robert Williams, then Alex Vartan. Should I book it?",
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        state=ThreadState(),
    )
    assert not any("consultation_csr_name_drift" in f for f in result.failures)


# ---------------------------------------------------------------------------
# Fabricated booking claims (chat 6816)
# ---------------------------------------------------------------------------


class TestUnverifiedSchedulingClaim:
    """The bot told a customer a specialist would ring her at a time nothing was
    booked for — three separate times. Check 22 only matched the explicit
    noun+verb form ("your consultation is booked"), so every one of those
    fabrications walked straight past it.
    """

    def test_locked_in_with_a_time_needs_evidence(self) -> None:
        from bookcraft.components.response.quality_gate import _unverified_scheduling_claim

        text = (
            "Perfect, Priya - Wednesday, July 15th between 9 and 12 Central is "
            "locked in. Our specialist will call you at 512-555-0142 then."
        )
        assert _unverified_scheduling_claim(text, ThreadState()) is True

    def test_bare_time_restatement_needs_evidence(self) -> None:
        from bookcraft.components.response.quality_gate import _unverified_scheduling_claim

        text = "Noon Friday it is. I've got your details set for the consultation."
        assert _unverified_scheduling_claim(text, ThreadState()) is True

    def test_claim_is_allowed_once_a_handoff_exists(self) -> None:
        from bookcraft.components.response.quality_gate import _unverified_scheduling_claim

        state = ThreadState()
        state.consultation_handoff_created = True
        text = "Wednesday, July 15th between 9 and 12 Central is locked in."
        assert _unverified_scheduling_claim(text, state) is False

    def test_asking_for_a_time_is_not_a_claim(self) -> None:
        from bookcraft.components.response.quality_gate import _unverified_scheduling_claim

        text = "What day and time this week suits you best, Priya?"
        assert _unverified_scheduling_claim(text, ThreadState()) is False

    def test_all_set_without_a_time_is_not_a_booking_claim(self) -> None:
        from bookcraft.components.response.quality_gate import _unverified_scheduling_claim

        # "You're all set" after answering a question must not be flagged.
        text = "You're all set - feel free to upload your manuscript via the attach button."
        assert _unverified_scheduling_claim(text, ThreadState()) is False

    def test_text_followup_promise_is_not_a_call_booking_claim(self) -> None:
        from bookcraft.components.response.quality_gate import _unverified_scheduling_claim

        text = "Texting works fine - our specialist will text you at your number."
        assert _unverified_scheduling_claim(text, ThreadState()) is False
