from __future__ import annotations

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.rag.query_builder import RAGQuery, RAGQueryBuilder
from bookcraft.components.response.planner import ResponsePlan
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory

_builder = RAGQueryBuilder()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _intent(
    *,
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: ServiceCategory | None = None,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.90,
        rationale="test",
        evidence=[],
    )


def _pack(
    *,
    active_service: str | None = None,
    active_genre: str | None = None,
    manuscript_status: str | None = None,
) -> ContextPack:
    return ContextPack(
        active_service=active_service,
        active_genre=active_genre,
        manuscript_status=manuscript_status,
    )


def _plan(
    *,
    primary_goal: str = "continue_discovery",
    next_question: str | None = None,
) -> ResponsePlan:
    return ResponsePlan(primary_goal=primary_goal, next_question=next_question)


# ---------------------------------------------------------------------------
# Base message
# ---------------------------------------------------------------------------


def test_query_includes_base_message() -> None:
    result = _builder.build(
        message="Tell me about your services.",
        intent=_intent(),
    )
    assert "Tell me about your services." in result.query_text


def test_query_text_is_non_empty_for_any_input() -> None:
    result = _builder.build(message="Hi", intent=_intent())
    assert result.query_text.strip()


# ---------------------------------------------------------------------------
# Active service enrichment
# ---------------------------------------------------------------------------


def test_query_includes_active_service_human_name() -> None:
    pack = _pack(active_service="cover_design_illustration")
    result = _builder.build(message="what do you offer?", intent=_intent(), context_pack=pack)
    assert "cover design" in result.query_text.lower()


def test_query_service_filter_set() -> None:
    pack = _pack(active_service="cover_design_illustration")
    result = _builder.build(message="?", intent=_intent(), context_pack=pack)
    assert result.filters.get("service_category") == "cover_design_illustration"


def test_service_in_source_terms() -> None:
    pack = _pack(active_service="editing_proofreading")
    result = _builder.build(message="help", intent=_intent(), context_pack=pack)
    assert "editing_proofreading" in result.source_terms


# ---------------------------------------------------------------------------
# Genre enrichment
# ---------------------------------------------------------------------------


def test_query_includes_genre() -> None:
    pack = _pack(active_genre="children's fiction")
    result = _builder.build(message="book", intent=_intent(), context_pack=pack)
    assert "children's fiction" in result.query_text


def test_genre_filter_set() -> None:
    pack = _pack(active_genre="fantasy")
    result = _builder.build(message="x", intent=_intent(), context_pack=pack)
    assert result.filters.get("genre") == "fantasy"


# ---------------------------------------------------------------------------
# Manuscript status enrichment
# ---------------------------------------------------------------------------


def test_query_includes_manuscript_status_human_phrase() -> None:
    pack = _pack(manuscript_status="completed_draft")
    result = _builder.build(message="x", intent=_intent(), context_pack=pack)
    assert "completed" in result.query_text.lower() or "draft" in result.query_text.lower()


def test_manuscript_status_in_source_terms() -> None:
    pack = _pack(manuscript_status="completed_draft")
    result = _builder.build(message="x", intent=_intent(), context_pack=pack)
    assert "completed_draft" in result.source_terms


# ---------------------------------------------------------------------------
# Response plan enrichment
# ---------------------------------------------------------------------------


def test_query_includes_primary_goal_phrase() -> None:
    plan = _plan(primary_goal="cover_design_scoping")
    result = _builder.build(message="?", intent=_intent(), response_plan=plan)
    assert "cover design" in result.query_text.lower()


def test_query_includes_next_question_phrase() -> None:
    plan = _plan(next_question="cover_style")
    result = _builder.build(message="?", intent=_intent(), response_plan=plan)
    assert "cover style" in result.query_text.lower()


def test_query_includes_pricing_goal_phrase() -> None:
    plan = _plan(primary_goal="pricing_scoping")
    result = _builder.build(message="cost", intent=_intent(), response_plan=plan)
    assert "pricing" in result.query_text.lower() or "estimate" in result.query_text.lower()


# ---------------------------------------------------------------------------
# No ghostwriting when cover design is active
# ---------------------------------------------------------------------------


def test_query_does_not_include_ghostwriting_when_cover_design_active() -> None:
    pack = _pack(active_service="cover_design_illustration")
    result = _builder.build(
        message="what is the process?",
        intent=_intent(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        context_pack=pack,
    )
    assert "ghostwriting" not in result.query_text.lower()


# ---------------------------------------------------------------------------
# Intent filter
# ---------------------------------------------------------------------------


def test_intent_query_type_in_filters() -> None:
    result = _builder.build(
        message="x",
        intent=_intent(query=QueryIntentType.SERVICE_QUESTION),
    )
    assert result.filters.get("query_intent") == "service_question"


def test_allowed_for_response_filter_always_true() -> None:
    result = _builder.build(message="x", intent=_intent())
    assert result.filters.get("allowed_for_response") is True


# ---------------------------------------------------------------------------
# Full spec example
# ---------------------------------------------------------------------------


def test_full_context_example_from_spec() -> None:
    """
    Spec example:
      message: "Its fiction children book as I told you."
      active_service: cover_design_illustration
      active_genre: children's fiction
      manuscript_status: completed_draft
      primary_goal: cover_design_scoping
      next_question: cover_style
    Expected query contains: cover design, children's fiction, completed draft, cover style
    Must NOT contain: ghostwriting
    """
    pack = _pack(
        active_service="cover_design_illustration",
        active_genre="children's fiction",
        manuscript_status="completed_draft",
    )
    plan = _plan(primary_goal="cover_design_scoping", next_question="cover_style")

    result = _builder.build(
        message="Its fiction children book as I told you.",
        intent=_intent(
            query=QueryIntentType.SERVICE_QUESTION,
            service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        ),
        context_pack=pack,
        response_plan=plan,
    )

    text = result.query_text.lower()
    assert "cover design" in text, f"Expected 'cover design'; query: {result.query_text}"
    assert "children's fiction" in text, f"Expected genre; query: {result.query_text}"
    assert "completed" in text or "draft" in text, f"Expected status; query: {result.query_text}"
    assert "cover style" in text, f"Expected 'cover style'; query: {result.query_text}"
    assert "ghostwriting" not in text, f"ghostwriting must not appear; query: {result.query_text}"


# ---------------------------------------------------------------------------
# Schema properties
# ---------------------------------------------------------------------------


def test_rag_query_is_typed_model() -> None:
    result = _builder.build(message="test", intent=_intent())
    assert isinstance(result, RAGQuery)
    assert isinstance(result.query_text, str)
    assert isinstance(result.filters, dict)
    assert isinstance(result.source_terms, list)
    assert isinstance(result.audit, list)


def test_audit_trail_populated() -> None:
    result = _builder.build(message="test", intent=_intent())
    assert len(result.audit) >= 1
    assert any("rag_query" in a for a in result.audit)


def test_audit_records_all_context_fields() -> None:
    pack = _pack(
        active_service="cover_design_illustration",
        active_genre="fantasy",
        manuscript_status="completed_draft",
    )
    plan = _plan(primary_goal="cover_design_scoping", next_question="cover_style")
    result = _builder.build(message="help", intent=_intent(), context_pack=pack, response_plan=plan)
    audit_str = " ".join(result.audit)
    assert "service" in audit_str
    assert "genre" in audit_str
    assert "manuscript_status" in audit_str
    assert "primary_goal" in audit_str
    assert "next_question" in audit_str


# ---------------------------------------------------------------------------
# Empty / minimal context
# ---------------------------------------------------------------------------


def test_no_context_pack_still_builds_query() -> None:
    result = _builder.build(
        message="Tell me about editing",
        intent=_intent(),
        context_pack=None,
        response_plan=None,
    )
    assert "Tell me about editing" in result.query_text
    assert result.filters.get("allowed_for_response") is True


def test_no_duplicate_terms_in_query() -> None:
    pack = _pack(active_service="cover_design_illustration", active_genre="cover design")
    result = _builder.build(message="cover design", intent=_intent(), context_pack=pack)
    parts = result.query_text.split()
    # Query text should not be excessively repetitive.
    assert len(parts) < 50, "Query text should be concise"


# ===========================================================================
# Required tests (exact names from spec)
# ===========================================================================


def test_cover_design_children_fiction_query_uses_context() -> None:
    """
    Full spec example:
      message="Its fiction children book as I told you."
      active_service=cover_design_illustration
      active_genre=children's fiction
      manuscript_status=completed_draft
      primary_goal=cover_design_scoping
      next_question=cover_style
    Expected: cover design wording, children's fiction, completed draft, cover style.
    Must NOT contain: ghostwriting.
    """
    pack = _pack(
        active_service="cover_design_illustration",
        active_genre="children's fiction",
        manuscript_status="completed_draft",
    )
    plan = _plan(primary_goal="cover_design_scoping", next_question="cover_style")

    result = _builder.build(
        message="Its fiction children book as I told you.",
        intent=_intent(
            query=QueryIntentType.SERVICE_QUESTION,
            service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        ),
        context_pack=pack,
        response_plan=plan,
    )

    text = result.query_text.lower()
    assert "cover design" in text or "cover_design_illustration" in text, (
        f"Expected cover-design wording; query: {result.query_text}"
    )
    assert "children's fiction" in text, f"Expected genre; query: {result.query_text}"
    assert "completed" in text or "draft" in text, (
        f"Expected manuscript status; query: {result.query_text}"
    )
    assert "cover style" in text, (
        f"Expected cover-style phrase from next_question; query: {result.query_text}"
    )
    assert "ghostwriting" not in text, f"ghostwriting must not appear; query: {result.query_text}"


def test_query_filters_include_active_service_and_intent() -> None:
    """Both service_category and query_intent must appear in filters when known."""
    pack = _pack(active_service="cover_design_illustration")
    result = _builder.build(
        message="what do you offer?",
        intent=_intent(
            query=QueryIntentType.SERVICE_QUESTION,
            service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        ),
        context_pack=pack,
    )

    assert result.filters.get("service_category") == "cover_design_illustration", (
        f"service_category missing from filters: {result.filters}"
    )
    assert result.filters.get("query_intent") == "service_question", (
        f"query_intent missing from filters: {result.filters}"
    )
    assert result.filters.get("allowed_for_response") is True


def test_query_excludes_forbidden_reasks() -> None:
    """
    When genre and manuscript_stage are in forbidden_reasks, the query must
    include the KNOWN VALUES (useful for retrieval) but must not add QUESTION
    FORMS like 'what genre' or 'manuscript stage' as a question phrase.
    """
    from bookcraft.components.context.schemas import ContextPack as _CP

    pack = _CP(
        active_service="cover_design_illustration",
        active_genre="children's fiction",
        manuscript_status="completed_draft",
        forbidden_reasks=["genre", "what genre", "manuscript_stage", "draft status"],
    )
    result = _builder.build(
        message="Tell me about cover options.",
        intent=_intent(),
        context_pack=pack,
    )

    text = result.query_text.lower()

    # Known VALUES should still appear for retrieval relevance.
    assert "children's fiction" in text, (
        f"Genre value should be in query for retrieval; query: {result.query_text}"
    )

    # QUESTION FORMS of the forbidden labels must not appear.
    assert "what genre" not in text, (
        f"'what genre' question form must not appear; query: {result.query_text}"
    )
    assert "manuscript stage?" not in text, (
        f"'manuscript stage?' question must not appear; query: {result.query_text}"
    )


def test_pricing_query_includes_quote_context() -> None:
    """
    For a pricing intent with ghostwriting service, fantasy genre, and
    word_or_page_count as the next missing fact, the query should include
    pricing/quote context plus the service, genre, and length hint.
    """
    pack = _pack(active_service="ghostwriting", active_genre="fantasy")
    plan = _plan(primary_goal="pricing_scoping", next_question="word_or_page_count")

    result = _builder.build(
        message="How much does it cost?",
        intent=_intent(
            query=QueryIntentType.PRICING_QUESTION,
            service=ServiceCategory.GHOSTWRITING,
        ),
        context_pack=pack,
        response_plan=plan,
    )

    text = result.query_text.lower()
    assert "ghostwriting" in text, f"Expected 'ghostwriting'; query: {result.query_text}"
    assert "fantasy" in text, f"Expected 'fantasy'; query: {result.query_text}"
    assert "pricing" in text or "estimate" in text or "cost" in text, (
        f"Expected pricing/estimate wording; query: {result.query_text}"
    )
    assert "word" in text or "page" in text or "count" in text, (
        f"Expected word/page-count phrase from next_question; query: {result.query_text}"
    )


def test_empty_context_falls_back_to_message() -> None:
    """With no context_pack or response_plan, the query uses only the raw message."""
    message = "Tell me about your ghostwriting services."
    result = _builder.build(
        message=message,
        intent=_intent(),
        context_pack=None,
        response_plan=None,
    )

    assert message in result.query_text, (
        f"Original message must be in query; query: {result.query_text}"
    )
    # At minimum the base-message audit entry should exist.
    assert any("base_message" in a for a in result.audit), (
        f"Expected base_message in audit; got: {result.audit}"
    )
    # No additional service/genre/status terms should appear.
    assert not result.source_terms, (
        f"source_terms should be empty without context; got: {result.source_terms}"
    )


def test_internal_terms_are_removed() -> None:
    """
    When the user message contains internal implementation words
    (backend, classifier, RAG, tool_governance, action_plan) they must be
    stripped from query_text and must not appear in source_terms.
    """
    message = "The backend classifier and RAG tool_governance action_plan will help."
    result = _builder.build(
        message=message,
        intent=_intent(),
        context_pack=None,
        response_plan=None,
    )

    text = result.query_text.lower()
    for forbidden in ("backend", "classifier", "rag", "tool_governance", "action_plan"):
        assert forbidden not in text, (
            f"Internal term '{forbidden}' must be stripped; query: {result.query_text}"
        )

    for term in result.source_terms:
        for forbidden in ("backend", "classifier", "RAG", "tool_governance", "action_plan"):
            assert forbidden.lower() not in term.lower(), (
                f"Internal term '{forbidden}' must not appear in source_terms"
            )
