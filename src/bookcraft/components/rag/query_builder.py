from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.planner import ResponsePlan

# Human-readable service names used in query text.
_SERVICE_NAMES: dict[str, str] = {
    "ghostwriting": "ghostwriting",
    "editing_proofreading": "editing proofreading",
    "cover_design_illustration": "cover design illustration",
    "interior_formatting": "interior formatting",
    "publishing_distribution": "publishing distribution",
    "marketing_promotion": "marketing promotion",
    "audiobook_production": "audiobook production",
    "author_website": "author website",
    "video_trailer": "video trailer",
    "fine_art_monograph": "fine art monograph publishing",
    "catalog_transition": "catalog transition rights recovery",
    "publishing_partnership": "hybrid publishing partnership",
    "author_brand_platform": "author brand platform strategy",
    "translation_foreign_rights": "translation foreign rights localization",
    "special_collector_editions": "special collector editions",
}

# Fact-key → context phrase for query enrichment.
_FACT_KEY_PHRASES: dict[str, str] = {
    "cover_style": "cover style visual direction",
    "word_or_page_count": "word count page count manuscript length",
    "genre": "genre book category",
    "manuscript_stage": "manuscript stage draft status",
    "deadline": "deadline launch timeline",
    "services": "service options",
}

# Goal → context phrase for query enrichment.
_GOAL_PHRASES: dict[str, str] = {
    "cover_design_scoping": "cover design scope visual direction",
    "pricing_scoping": "pricing estimate cost breakdown",
    "consultation_scoping": "consultation scope services review",
    "document_scoping": "NDA agreement document",
    "portfolio_matching": "portfolio samples examples",
    "continue_discovery": "service discovery next steps",
    "clarify_intent": "intent clarification",
    "safe_blocked_action": "",
    "clarify_project_scope": "project scope clarification same or new book",
}

# Manuscript status → human-readable phrase.
_MANUSCRIPT_STATUS_PHRASES: dict[str, str] = {
    "idea_only": "idea only concept",
    "outline": "outline planned",
    "partial_draft": "partial draft in progress",
    "completed_draft": "completed draft finished manuscript",
    "edited": "edited polished manuscript",
    "published": "published book",
    "unknown": "",
}

# Internal implementation terms that must never appear in RAG query text.
_INTERNAL_TERM_RE = re.compile(
    r"\b(?:backend|classifier|runtime\s+atoms|provider\s+votes|RAG|tool_governance"
    r"|action_plan|deterministic\s+engine|quote\s+engine|ContextArbiter)\b",
    re.IGNORECASE,
)

# Known-fact paths already captured by the scalar ContextPack fields — skip to
# avoid duplicating content that is already added via active_service/genre/status.
_COVERED_FACT_PATHS = frozenset(
    {
        "project.genre",
        "project.manuscript_status",
        "service.active",
    }
)

# Maximum query-text length (characters).  Keeps the BM25 query readable and
# avoids sending overly large payloads to Elasticsearch.
_MAX_QUERY_LEN = 400


class RAGQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: str
    filters: dict[str, str | list[str] | bool] = Field(default_factory=dict)
    source_terms: list[str] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


class RAGQueryBuilder:
    """Builds a context-aware RAG query from the current turn's full context."""

    def build(
        self,
        *,
        message: str,
        intent: IntentVote,
        context_pack: ContextPack | None = None,
        response_plan: ResponsePlan | None = None,
    ) -> RAGQuery:
        audit: list[str] = []
        parts: list[str] = []
        source_terms: list[str] = []
        filters: dict[str, str | list[str] | bool] = {"allowed_for_response": True}

        # Base: include current user message, stripped of internal implementation terms.
        stripped = _INTERNAL_TERM_RE.sub("", message).strip()
        stripped = " ".join(stripped.split())  # collapse whitespace
        if stripped:
            parts.append(stripped)
            audit.append("rag_query:base_message")
        elif message.strip():
            audit.append("rag_query:base_message_sanitized_empty")

        # Intent filter — always set.
        filters["query_intent"] = intent.query_primary.value
        audit.append(f"rag_query:intent:{intent.query_primary.value}")

        if context_pack is not None:
            # Active service → human-readable name + ES filter.
            if context_pack.active_service:
                svc = context_pack.active_service
                human = _SERVICE_NAMES.get(svc, svc.replace("_", " "))
                parts.append(human)
                source_terms.append(svc)
                filters["service_category"] = svc
                audit.append(f"rag_query:service:{svc}")

            # Genre.
            if context_pack.active_genre:
                parts.append(context_pack.active_genre)
                source_terms.append(context_pack.active_genre)
                filters["genre"] = context_pack.active_genre
                audit.append(f"rag_query:genre:{context_pack.active_genre}")

            # Manuscript status → human phrase + ES filter.
            if context_pack.manuscript_status:
                phrase = _MANUSCRIPT_STATUS_PHRASES.get(
                    context_pack.manuscript_status,
                    context_pack.manuscript_status.replace("_", " "),
                )
                if phrase:
                    parts.append(phrase)
                source_terms.append(context_pack.manuscript_status)
                filters["manuscript_status"] = context_pack.manuscript_status
                audit.append(f"rag_query:manuscript_status:{context_pack.manuscript_status}")

            # Known facts — string values from facts not already covered above.
            for fact in context_pack.known_facts:
                if fact.path in _COVERED_FACT_PATHS:
                    continue
                val = fact.value
                # Only string values are useful for BM25 text retrieval.
                if not isinstance(val, str) or not val.strip():
                    continue
                clean = _INTERNAL_TERM_RE.sub("", val).strip()
                clean = " ".join(clean.split())
                if not clean:
                    continue
                if clean not in source_terms:
                    source_terms.append(clean)
                parts.append(clean)
                audit.append(f"rag_query:known_fact:{fact.path}:{clean[:30]}")

        if context_pack is not None:
            # Active project ID and event — added to filters/audit only (not query text)
            # so previous project facts never bleed into active retrieval.
            if context_pack.active_project_id:
                filters["active_project_id"] = context_pack.active_project_id
                audit.append(f"rag_query:project_id:{context_pack.active_project_id[:8]}")
            if context_pack.project_event:
                filters["project_event"] = context_pack.project_event
                audit.append(f"rag_query:project_event:{context_pack.project_event}")
            # Explicitly note when previous project facts are excluded from query text.
            if context_pack.previous_project_id:
                audit.append(
                    f"rag_query:previous_project_excluded:{context_pack.previous_project_id[:8]}"
                )
            # project_event==new_project: confirm active facts are scoped to new project.
            if context_pack.project_event == "new_project":
                audit.append("rag_query:new_project_scope_enforced")

        if response_plan is not None:
            # Primary goal context phrase.
            goal_phrase = _GOAL_PHRASES.get(response_plan.primary_goal, "")
            if goal_phrase:
                parts.append(goal_phrase)
                audit.append(f"rag_query:primary_goal:{response_plan.primary_goal}")

            # Next question → relevant context phrase.
            if response_plan.next_question:
                nq_phrase = _FACT_KEY_PHRASES.get(response_plan.next_question, "")
                if nq_phrase:
                    parts.append(nq_phrase)
                audit.append(f"rag_query:next_question:{response_plan.next_question}")

        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique_parts: list[str] = []
        for part in parts:
            if part not in seen:
                seen.add(part)
                unique_parts.append(part)

        query_text = " ".join(unique_parts)

        # Enforce maximum length at a word boundary.
        if len(query_text) > _MAX_QUERY_LEN:
            query_text = query_text[:_MAX_QUERY_LEN].rsplit(" ", 1)[0]
            audit.append(f"rag_query:truncated:{len(query_text)}")

        audit.append(f"rag_query:text_length:{len(query_text)}")

        return RAGQuery(
            query_text=query_text,
            filters=filters,
            source_terms=source_terms,
            audit=audit,
        )
