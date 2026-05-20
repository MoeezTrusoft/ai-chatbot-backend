from __future__ import annotations

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.rag.query_builder import RAGQueryBuilder
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory

_builder = RAGQueryBuilder()


def _intent(service: ServiceCategory | None = None) -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
    )


# ---------------------------------------------------------------------------
# 1. RAG query uses active project facts
# ---------------------------------------------------------------------------


def test_rag_query_uses_active_project_facts() -> None:
    pack = ContextPack(
        active_project_id="proj-active-001",
        project_event="same_project",
        active_service="cover_design_illustration",
        active_genre="fantasy",
    )
    result = _builder.build(
        message="Show me covers",
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        context_pack=pack,
    )
    assert "fantasy" in result.query_text, (
        f"Active project genre must appear in query, got {result.query_text}"
    )
    assert "cover design" in result.query_text.lower(), (
        f"Active project service must appear in query, got {result.query_text}"
    )
    assert result.filters.get("active_project_id") == "proj-active-001"


# ---------------------------------------------------------------------------
# 2. RAG query excludes previous project facts (new_project event)
# ---------------------------------------------------------------------------


def test_rag_query_excludes_previous_project_facts() -> None:
    pack = ContextPack(
        active_project_id="proj-new-002",
        project_event="new_project",
        previous_project_id="proj-old-001",
        active_service="editing_proofreading",
        # active_genre is None because new project has no known genre
        active_genre=None,
    )
    result = _builder.build(
        message="I need editing for my new book",
        intent=_intent(ServiceCategory.EDITING_PROOFREADING),
        context_pack=pack,
    )
    # Previous project genre must not appear in query text
    # (This is guaranteed because context_pack.active_genre=None → not included)
    assert result.filters.get("active_project_id") == "proj-new-002"
    assert "rag_query:new_project_scope_enforced" in result.audit, (
        f"new_project scope audit must be present, got {result.audit}"
    )


# ---------------------------------------------------------------------------
# 3. RAG filters include active_project_id
# ---------------------------------------------------------------------------


def test_rag_filters_include_active_project_id() -> None:
    pack = ContextPack(
        active_project_id="proj-test-003",
        project_event="same_project",
    )
    result = _builder.build(
        message="Help with my book",
        intent=_intent(),
        context_pack=pack,
    )
    assert result.filters.get("active_project_id") == "proj-test-003"
    assert any("project_id" in a or "proj-tes" in a for a in result.audit), (
        f"project_id must appear in audit, got {result.audit}"
    )


# ---------------------------------------------------------------------------
# 4. RAG audit notes previous project exclusion
# ---------------------------------------------------------------------------


def test_rag_audit_notes_previous_project_exclusion() -> None:
    pack = ContextPack(
        active_project_id="proj-new-004",
        project_event="project_switch",
        previous_project_id="proj-old-004",
    )
    result = _builder.build(
        message="Let's continue",
        intent=_intent(),
        context_pack=pack,
    )
    assert any("previous_project_excluded" in a for a in result.audit), (
        f"Audit must note previous project exclusion, got {result.audit}"
    )
