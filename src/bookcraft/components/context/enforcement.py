"""ContextEnforcementGate — enforces detected signals into active context decisions.

Signals (intent, negation, delegation, slot resolution, consultation objective,
current question priority) are often detected correctly but not enforced into
the final response plan or context pack. This gate produces a unified decision
that downstream components (ContextPackBuilder, ResponsePlanner, QualityGate)
must obey.

Priority order (highest to lowest):
  1. Explicit user correction (service / slot / platform / format negation)
  2. Current question priority (user asked something specific)
  3. Consultation request / expert guidance request
  4. Negation targets from preprocessor
  5. Delegated / declined / unknown slot
  6. Old active context (preserved but deprioritised)

Engines compute. Claude writes.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.leads.contact_utils import contact_is_ready

# Detects explicit "not published yet" corrections so we can clear a wrongly
# inferred "published" manuscript status from state.
_NOT_PUBLISHED_YET_RE = re.compile(
    r"\b(?:"
    r"(?:not|isn'?t|haven'?t|has\s+not|have\s+not)\s+(?:been\s+)?published\s+yet|"
    r"not\s+(?:yet\s+)?published|"
    r"book\s+(?:is\s+)?not\s+published|"
    r"(?:my|the)\s+book\s+(?:hasn'?t|has\s+not|is\s+not|isn'?t)\s+(?:been\s+)?published|"
    r"i\s+(?:said|told\s+you)\s+(?:my|the)\s+book\s+is\s+not|"
    r"(?:still\s+)?(?:unpublished|unfinished|not\s+done|not\s+ready\s+to\s+publish)"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Delegation patterns (augmented beyond delegation.py)
# ---------------------------------------------------------------------------

_DELEGATION_FULL_RE = re.compile(
    r"\b(?:you\s+(?:design|guys\s+design|come\s+up\s+with|decide|suggest|can\s+decide)|"
    r"come\s+up\s+with\s+(?:your\s+)?(?:own|it)|"
    r"your\s+(?:team\s+can\s+decide|choice|call)|"
    r"i(?:'ll|\s+will)\s+(?:approve|review\s+it\s+later|accept\s+any)|"
    r"i\s+can\s+request\s+changes|"
    r"no\s+(?:design|idea|style)\s+in\s+mind|"
    r"nothing\s+in\s+mind|"
    r"use\s+your\s+(?:own\s+)?(?:creativity|judgment|expertise|best\s+judgment)|"
    r"bookcraft\s+can\s+decide|"
    r"whatever\s+(?:you\s+think|is\s+best)|"
    r"i\s+trust\s+your\s+(?:team|judgment))\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Unknown / "no idea" patterns for slots
# ---------------------------------------------------------------------------

_UNKNOWN_FULL_RE = re.compile(
    r"\b(?:(?:again\s+)?no\s+idea|(?:again\s+)?no\s+clue|"
    r"not\s+sure|unsure|i\s+don'?t\s+know|i\s+do\s+not\s+know|"
    r"i\s+can'?t\s+tell|haven'?t\s+decided|"
    r"(?:again\s+)?i\s+don'?t\s+(?:have\s+that|remember)|"
    r"listen\s+to\s+my\s+story\s+and\s+suggest|"
    r"you\s+suggest\s+(?:to\s+)?me|guide\s+me|"
    r"you\s+(?:recommend|advise)\s+me)\b",
    re.IGNORECASE,
)

_REPEATED_PREFIX_RE = re.compile(r"\bagain\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Consultation / expert guidance patterns
# ---------------------------------------------------------------------------

_CONSULTATION_RE = re.compile(
    r"\b(?:listen\s+to\s+my\s+story\s+and\s+suggest|"
    r"can\s+someone\s+guide\s+me|"
    r"i\s+want\s+(?:your\s+)?expert\s+(?:opinion|advice|guidance)|"
    r"i\s+need\s+a\s+consultant|"
    r"i\s+want\s+bookcraft\s+to\s+decide|"
    r"can\s+your\s+specialist\s+(?:advise|help)|"
    r"(?:can\s+i\s+)?talk\s+to\s+someone|"
    r"speak\s+to\s+(?:a\s+)?(?:specialist|consultant|expert)|"
    r"schedule\s+a\s+(?:call|consultation)|"
    r"book\s+a\s+(?:call|consultation)|"
    r"free\s+consultation)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Service correction patterns
# ---------------------------------------------------------------------------

_SERVICE_CORRECTION_RE = re.compile(
    r"\b(?:not\s+ghostwriting|forget\s+ghostwriting|"
    r"i\s+(?:asked|was\s+asking|meant)\s+about\s+(?:distribution|publishing|editing|cover)|"
    r"i\s+meant\s+(?:distribution|publishing|editing|cover)|"
    r"instead\s+i\s+need\s+(?:distribution|publishing|editing|cover)|"
    r"actually\s+i\s+need\s+(?:distribution|publishing|editing|cover))\b",
    re.IGNORECASE,
)

# Service → canonical key mapping
_SERVICE_CORRECTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdistribution\b|\bpublishing\b|\bpublish\b", re.I), "publishing_distribution"),
    (re.compile(r"\bediting\b|\bproofread", re.I), "editing_proofreading"),
    (re.compile(r"\bcover\s+design\b|\bcover\b", re.I), "cover_design_illustration"),
    (re.compile(r"\bghostwriting\b|\bghost\s+writer\b", re.I), "ghostwriting"),
    (re.compile(r"\bformatting\b|\binterior\b", re.I), "interior_formatting"),
    (re.compile(r"\bmarketing\b", re.I), "marketing_promotion"),
]

# Negated service patterns — explicit "not X" style
_NEGATED_SERVICE_RE = re.compile(
    r"\b(?:not|no|don'?t\s+(?:want|need)|forget|instead\s+of)\s+"
    r"(?:ghostwriting|editing|cover\s+design|formatting|publishing|marketing)\b",
    re.IGNORECASE,
)

# Completion-style service negations — "done with editing", "already edited", etc.
# These mean the author has ALREADY completed that service and doesn't need it.
_COMPLETED_SERVICE_RE = re.compile(
    r"\b(?:"
    r"done\s+with\s+(?:editing|proofreading|ghostwriting|formatting|writing|(?:the\s+)?cover(?:\s+design)?)|"
    r"(?:editing|proofreading|writing|formatting)\s+(?:is\s+)?(?:done|complete|finished|ready)|"
    r"(?:the\s+)?(?:book\s+)?cover(?:\s+design)?\s+(?:is\s+)?(?:done|complete|finished|ready)|"
    r"cover(?:\s+design)?\s+(?:is\s+)?(?:done|complete|finished|ready)|"
    r"already\s+(?:edited|proofread|written|formatted|published|(?:have\s+a\s+)?cover(?:\s+design)?)|"
    r"finished\s+(?:editing|proofreading|writing|formatting|(?:the\s+)?cover(?:\s+design)?)|"
    r"editing\s+(?:and\s+proofreading\s+)?(?:is\s+)?(?:done|complete|finished)"
    r")\b",
    re.IGNORECASE,
)

_COMPLETED_SERVICE_MAP: dict[re.Pattern[str], str] = {
    re.compile(r"\b(?:editing|proofreading)\b", re.I): "editing_proofreading",
    re.compile(r"\bghostwriting\b|\bwriting\b", re.I): "ghostwriting",
    re.compile(r"\bformatting\b", re.I): "interior_formatting",
    re.compile(r"\bpublishing\b", re.I): "publishing_distribution",
    re.compile(r"\bcover(?:\s+design)?\b", re.I): "cover_design_illustration",
}

# ---------------------------------------------------------------------------
# Platform / format negation patterns
# ---------------------------------------------------------------------------

_NEGATED_PLATFORM_RE = re.compile(
    r"\b(?:not?|no|don'?t\s+(?:want|use)|skip|avoid)\s+"
    r"(?:amazon\s+kdp|kdp|amazon|ingramspark|ingram|kobo|apple\s+books|google\s+play)\b",
    re.IGNORECASE,
)

_NEGATED_FORMAT_RE = re.compile(
    r"\b(?:not?|no|don'?t\s+(?:want|need))\s+"
    r"(?:hardcover|hardback|audiobook|paperback|ebook|large\s+print)\b",
    re.IGNORECASE,
)

_PLATFORM_MAP: dict[str, str] = {
    "amazon": "amazon_kdp",
    "amazon kdp": "amazon_kdp",
    "kdp": "amazon_kdp",
    "ingramspark": "ingramspark",
    "ingram": "ingramspark",
    "kobo": "kobo",
    "apple books": "apple_books",
    "google play": "google_play_books",
}

_FORMAT_MAP: dict[str, str] = {
    "hardcover": "hardcover",
    "hardback": "hardcover",
    "audiobook": "audiobook",
    "paperback": "paperback",
    "ebook": "ebook",
    "large print": "large_print",
}

# ---------------------------------------------------------------------------
# False-genre inference patterns — terms that should NOT lock in fiction
# ---------------------------------------------------------------------------

_WEAK_GENRE_TERMS_RE = re.compile(
    r"\b(?:story|book|manuscript|autobiography|personal\s+story|"
    r"business\s+idea|idea|project|writing\s+project)\b",
    re.IGNORECASE,
)

_MEMOIR_CUES_RE = re.compile(
    r"\b(?:autobiography|my\s+life|life\s+story|personal\s+(?:story|narrative)|memoir)\b",
    re.IGNORECASE,
)

# Scoping slots suppressed during consultation / current question enforcement
_SCOPING_SLOTS: frozenset[str] = frozenset(
    {
        "cover_style",
        "word_or_page_count",
        "word_count",
        "page_count",
        "deadline",
        "genre",
        "manuscript_stage",
    }
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ContextEnforcementDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_service: str | None = None
    removed_services: list[str] = Field(default_factory=list)
    negated_services: list[str] = Field(default_factory=list)
    declined_slots: list[str] = Field(default_factory=list)
    delegated_slots: list[str] = Field(default_factory=list)
    unknown_slots: list[str] = Field(default_factory=list)
    negated_platforms: list[str] = Field(default_factory=list)
    negated_formats: list[str] = Field(default_factory=list)
    cleared_false_facts: list[str] = Field(default_factory=list)
    forced_primary_goal: str | None = None
    forced_next_question: str | None = None
    forced_current_question_type: str | None = None
    stale_context_terms: list[str] = Field(default_factory=list)
    forbidden_reasks: list[str] = Field(default_factory=list)
    state_updates: dict[str, Any] = Field(default_factory=dict)
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class ContextEnforcementGate:
    """
    Converts all detected signals into a single enforceable decision.

    Downstream (ContextPackBuilder, ResponsePlanner, QualityGate) must obey
    forced_primary_goal, forced_next_question, forbidden_reasks, and
    state_updates from this decision.
    """

    def enforce(
        self,
        *,
        text: str,
        intent: Any,  # IntentVote
        state: Any,  # ThreadState
        processed: Any | None = None,
        context_pack: Any | None = None,
        current_question_priority: Any | None = None,
        consultation_objective: Any | None = None,
        service_metadata_extraction: Any | None = None,
        negation_targets: Any | None = None,
        slot_resolution: Any | None = None,
        delegated_decision: Any | None = None,
    ) -> ContextEnforcementDecision:
        audit: list[str] = []
        forbidden_reasks: list[str] = []
        state_updates: dict[str, Any] = {}
        stale_context_terms: list[str] = []

        removed_services: list[str] = []
        negated_services: list[str] = []
        negated_platforms: list[str] = []
        negated_formats: list[str] = []
        cleared_false_facts: list[str] = []
        delegated_slots: list[str] = []
        declined_slots: list[str] = []
        unknown_slots: list[str] = []

        forced_primary_goal: str | None = None
        forced_next_question: str | None = None
        forced_current_question_type: str | None = None
        active_service: str | None = None

        # Gather contact/time readiness
        contact_ready = _contact_ready(state, context_pack)
        call_time_ready = bool(getattr(state, "preferred_call_time", None))

        # ── Priority 1: Explicit service correction ───────────────────────
        if _SERVICE_CORRECTION_RE.search(text):
            audit.append("signal:service_correction_detected")
            # Identify what's being negated
            neg_svc = _extract_negated_service(text)
            if neg_svc:
                negated_services.append(neg_svc)
                removed_services.append(neg_svc)
                stale_context_terms.append(neg_svc)
                audit.append(f"enforcement:service_negated:{neg_svc}")
            # Identify replacement service
            replacement = _extract_replacement_service(text, neg_svc)
            if replacement:
                active_service = replacement
                audit.append(f"enforcement:service_replacement:{replacement}")
            forced_primary_goal = "answer_current_question"
            audit.append("enforcement:service_correction")

        # ── Priority 2: Current question has priority ─────────────────────
        cqp_has_priority = current_question_priority is not None and getattr(
            current_question_priority, "has_priority", False
        )
        if cqp_has_priority:
            qt = getattr(current_question_priority, "question_type", None)
            forced_current_question_type = qt
            # Don't override forced_primary_goal from service correction
            if not forced_primary_goal:
                forced_primary_goal = "answer_current_question"
            # Suppress stale scoping slots
            for slot in _SCOPING_SLOTS:
                if slot not in forbidden_reasks:
                    forbidden_reasks.append(slot)
            stale_context_terms.extend(["cover_style", "word_or_page_count"])
            audit.append(f"enforcement:current_question_priority:{qt}")

        # ── Priority 3: Consultation request ─────────────────────────────
        query_primary = str(getattr(intent, "query_primary", "") or "")
        is_consultation_intent = query_primary == "consultation_request"
        is_consultation_text = bool(_CONSULTATION_RE.search(text))
        if is_consultation_intent or is_consultation_text:
            if not forced_primary_goal:
                if contact_ready and call_time_ready:
                    forced_primary_goal = "consultation_handoff_confirmation"
                    forced_next_question = None
                elif contact_ready:
                    forced_primary_goal = "consultation_time_capture"
                    forced_next_question = "preferred_call_time"
                else:
                    forced_primary_goal = "consultation_offer"
                    forced_next_question = "name_and_email_or_phone"
            # Suppress old scoping slots
            for slot in _SCOPING_SLOTS:
                if slot not in forbidden_reasks:
                    forbidden_reasks.append(slot)
            audit.append("enforcement:consultation_request")

        # ── Priority 4: Delegation of cover_style ────────────────────────
        is_delegation = bool(_DELEGATION_FULL_RE.search(text))
        if is_delegation:
            # Check if cover-related context or active slot is cover_style
            is_cover_context = _is_cover_context(text, state, context_pack, intent)
            if is_cover_context:
                if "cover_style" not in delegated_slots:
                    delegated_slots.append("cover_style")
                forbidden_reasks.extend(["cover_style", "visual direction", "cover style"])
                if not forced_primary_goal:
                    if contact_ready and call_time_ready:
                        forced_primary_goal = "consultation_handoff_confirmation"
                        forced_next_question = None
                    elif contact_ready:
                        forced_primary_goal = "consultation_time_capture"
                        forced_next_question = "preferred_call_time"
                    else:
                        forced_primary_goal = "process_explanation"
                        forced_next_question = "name_and_email_or_phone"
                audit.append("enforcement:delegated_slot:cover_style")
            else:
                # Generic delegation — bind to current pending slot
                pending = _pending_slot(state, context_pack)
                if pending and pending not in delegated_slots:
                    delegated_slots.append(pending)
                    forbidden_reasks.append(pending)
                    audit.append(f"enforcement:delegated_slot:{pending}")

        # ── Priority 5: Unknown/no-idea for word/page count ───────────────
        is_unknown = bool(_UNKNOWN_FULL_RE.search(text))
        if is_unknown:
            # Check if the unknown signal is about word/page count or is generic
            is_repeated = bool(_REPEATED_PREFIX_RE.search(text))
            slot_hint = _infer_unknown_slot(text, state, context_pack)

            if slot_hint == "word_or_page_count" or _mentions_count_context(text):
                if "word_or_page_count" not in unknown_slots:
                    unknown_slots.append("word_or_page_count")
                forbidden_reasks.extend(
                    ["word_or_page_count", "word count", "page count", "pages", "words"]
                )
                if is_repeated:
                    forbidden_reasks.extend(["word count", "page count"])
                    audit.append("enforcement:repeated_slot_refusal")
                if not forced_primary_goal:
                    if contact_ready and call_time_ready:
                        forced_primary_goal = "consultation_handoff_confirmation"
                        forced_next_question = None
                    elif contact_ready:
                        forced_primary_goal = "consultation_time_capture"
                        forced_next_question = "preferred_call_time"
                    else:
                        forced_primary_goal = "consultation_offer"
                        forced_next_question = "name_and_email_or_phone"
                audit.append("enforcement:unknown_slot:word_or_page_count")
            elif slot_hint:
                if slot_hint not in unknown_slots:
                    unknown_slots.append(slot_hint)
                forbidden_reasks.append(slot_hint)
                audit.append(f"enforcement:unknown_slot:{slot_hint}")

        # ── Priority 6: Negated platforms from metadata extraction ─────────
        if service_metadata_extraction is not None:
            sme_candidates = getattr(service_metadata_extraction, "candidates", {}) or {}
            for _svc_key, cands in sme_candidates.items():
                for cand in cands:
                    if isinstance(cand, dict) and cand.get("certainty") == "negated":
                        key = cand.get("key", "")
                        val = cand.get("value", "")
                        if key == "publishing_platforms" and val:
                            platform_key = str(val)
                            if platform_key not in negated_platforms:
                                negated_platforms.append(platform_key)
                            audit.append(f"enforcement:negated_platform:{platform_key}")
                        elif key == "book_formats" and val:
                            fmt_key = str(val)
                            if fmt_key not in negated_formats:
                                negated_formats.append(fmt_key)
                            audit.append(f"enforcement:negated_format:{fmt_key}")

        # Also detect platform/format negation directly from text
        for m in _NEGATED_PLATFORM_RE.finditer(text):
            matched = m.group(0)
            for key, canonical in _PLATFORM_MAP.items():
                if key in matched.lower() and canonical not in negated_platforms:
                    negated_platforms.append(canonical)
                    audit.append(f"enforcement:negated_platform:{canonical}")

        for m in _NEGATED_FORMAT_RE.finditer(text):
            matched = m.group(0)
            for key, canonical in _FORMAT_MAP.items():
                if key in matched.lower() and canonical not in negated_formats:
                    negated_formats.append(canonical)
                    audit.append(f"enforcement:negated_format:{canonical}")

        # Remove negated platforms from state's confirmed publishing_platforms
        if negated_platforms:
            current_pp = list(getattr(state, "publishing_platforms", None) or [])
            cleaned_pp = [p for p in current_pp if p not in negated_platforms]
            if cleaned_pp != current_pp:
                state_updates["publishing_platforms"] = cleaned_pp
                audit.append(f"state:publishing_platforms_cleaned:{negated_platforms}")

        # Remove negated formats from state's confirmed book_formats
        if negated_formats:
            current_bf = list(getattr(state.project, "book_formats", None) or [])
            cleaned_bf = [f for f in current_bf if f not in negated_formats]
            if cleaned_bf != current_bf:
                state_updates["book_formats"] = cleaned_bf
                audit.append(f"state:book_formats_cleaned:{negated_formats}")

        # ── Priority 7: False/weak genre clearing ─────────────────────────
        current_genre = _get_confirmed_genre(state)
        if current_genre == "fiction":
            # Check if genre was inferred from weak terms only
            genre_source = _get_genre_source(state)
            is_weak_inference = genre_source in {
                "ai_extracted",
                "system",
                None,
            } and _only_weak_genre_terms(text, state)
            if is_weak_inference:
                cleared_false_facts.append("project.genre")
                state_updates["clear_genre"] = True
                audit.append("enforcement:cleared_false_fiction_genre")

        # Memoir cue — override fiction toward memoir candidate
        if _MEMOIR_CUES_RE.search(text) and current_genre == "fiction":
            cleared_false_facts.append("project.genre")
            state_updates["clear_genre"] = True
            state_updates["genre_candidate"] = "memoir"
            audit.append("enforcement:autobiography_not_fiction")

        # ── Manuscript status correction: "not published yet" ─────────────
        # When the user explicitly says their book is NOT published, clear any
        # previously extracted "published" status so the bot stops reverting to
        # "your book is already published" on subsequent turns.
        _current_ms = getattr(
            getattr(getattr(state, "project", None), "manuscript_status", None),
            "value", None,
        )
        if (
            _current_ms in {"published", "already_published"}
            and _NOT_PUBLISHED_YET_RE.search(text)
        ):
            state_updates["clear_manuscript_status"] = True
            cleared_false_facts.append("project.manuscript_status")
            stale_context_terms.append("published")
            audit.append("enforcement:cleared_false_published_status")

        # ── Apply removed services to state ────────────────────────────────
        if negated_services:
            # If active service is negated, clear it (use replacement if found)
            current_active = _get_active_service(state, context_pack)
            if current_active and current_active in negated_services:
                if active_service is None:
                    active_service = _extract_replacement_service(text, current_active)
                stale_context_terms.append(current_active)
                state_updates["clear_active_service"] = True
                audit.append(f"state:active_service_cleared:{current_active}")

        if not audit:
            audit.append("no_enforcement_needed")

        return ContextEnforcementDecision(
            active_service=active_service,
            removed_services=removed_services,
            negated_services=negated_services,
            declined_slots=declined_slots,
            delegated_slots=delegated_slots,
            unknown_slots=unknown_slots,
            negated_platforms=negated_platforms,
            negated_formats=negated_formats,
            cleared_false_facts=cleared_false_facts,
            forced_primary_goal=forced_primary_goal,
            forced_next_question=forced_next_question,
            forced_current_question_type=forced_current_question_type,
            stale_context_terms=list(dict.fromkeys(stale_context_terms)),
            forbidden_reasks=list(dict.fromkeys(forbidden_reasks)),
            state_updates=state_updates,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contact_ready(state: Any, context_pack: Any | None) -> bool:
    if context_pack is not None:
        status = getattr(context_pack, "contact_capture_status", None)
        if status == "ready":
            return True
    contact_info = getattr(state, "contact_info", None) or {}
    # Use sentinel-aware helper so redacted placeholders never count as "ready".
    return contact_is_ready(contact_info)


def _is_cover_context(text: str, state: Any, context_pack: Any | None, intent: Any) -> bool:
    lowered = text.casefold()
    if any(kw in lowered for kw in ("cover", "design", "visual", "style", "illustration")):
        return True
    active_svc = _get_active_service(state, context_pack)
    if active_svc == "cover_design_illustration":
        return True
    svc_primary = str(getattr(intent, "service_primary", "") or "")
    if "cover_design" in svc_primary:
        return True
    if context_pack is not None:
        allowed = getattr(context_pack, "allowed_next_questions", []) or []
        if "cover_style" in allowed:
            return True
    return False


def _pending_slot(state: Any, context_pack: Any | None) -> str | None:
    if context_pack is not None:
        allowed = getattr(context_pack, "allowed_next_questions", []) or []
        if allowed:
            return str(allowed[0])
        missing = getattr(context_pack, "missing_facts", []) or []
        if missing:
            return str(missing[0])
    return None


def _infer_unknown_slot(text: str, state: Any, context_pack: Any | None) -> str | None:
    lowered = text.casefold()
    if any(kw in lowered for kw in ("word", "page", "length", "count", "words", "pages")):
        return "word_or_page_count"
    if any(kw in lowered for kw in ("cover", "style", "visual", "design")):
        return "cover_style"
    if any(kw in lowered for kw in ("genre", "category", "type of book")):
        return "genre"
    if any(kw in lowered for kw in ("stage", "manuscript", "draft")):
        return "manuscript_stage"
    return _pending_slot(state, context_pack)


def _mentions_count_context(text: str) -> bool:
    lowered = text.casefold()
    return any(kw in lowered for kw in ("word", "page", "pages", "words", "count", "length"))


def _extract_negated_service(text: str) -> str | None:
    # Match service names after explicit negation words.
    neg_pattern = re.compile(
        r"\b(?:not|no|forget|instead\s+of)\s+"
        r"(ghostwriting|editing|cover\s+design|formatting|publishing|distribution|marketing)\b",
        re.I,
    )
    m = neg_pattern.search(text)
    if m:
        svc_word = m.group(1).casefold()
        for _pat, canonical in _SERVICE_CORRECTIONS:
            if _pat.search(svc_word):
                return canonical

    # Match completion-style negations: "done with editing", "editing is done", etc.
    if _COMPLETED_SERVICE_RE.search(text):
        for svc_pattern, canonical in _COMPLETED_SERVICE_MAP.items():
            if svc_pattern.search(text):
                return canonical

    return None


def _extract_replacement_service(text: str, excluded: str | None = None) -> str | None:
    for pattern, canonical in _SERVICE_CORRECTIONS:
        if pattern.search(text) and canonical != excluded:
            return canonical
    return None


def _get_active_service(state: Any, context_pack: Any | None) -> str | None:
    if context_pack is not None:
        svc = getattr(context_pack, "active_service", None)
        if svc:
            return str(svc)
    services_discussed = getattr(getattr(state, "project", None), "services_discussed", None) or []
    if services_discussed:
        last = services_discussed[-1]
        svc = getattr(getattr(last, "service", None), "value", None)
        return str(svc) if svc else None
    return None


def _get_confirmed_genre(state: Any) -> str | None:
    genre_field = getattr(getattr(state, "project", None), "genre", None)
    if genre_field is None:
        return None
    val = getattr(genre_field, "value", None)
    return str(val) if val else None


def _get_genre_source(state: Any) -> str | None:
    genre_field = getattr(getattr(state, "project", None), "genre", None)
    if genre_field is None:
        return None
    src = getattr(genre_field, "source", None)
    return str(src) if src else None


def _only_weak_genre_terms(text: str, state: Any) -> bool:
    """Return True if genre=fiction was inferred from only weak/ambiguous terms."""
    # If the current message contains explicit "fiction" affirmation, keep it
    if re.search(r"\bfiction\b", text, re.I):
        return False
    # If state has a confirmed explicit user-stated genre, keep it
    genre_field = getattr(getattr(state, "project", None), "genre", None)
    if genre_field is not None:
        source = str(getattr(genre_field, "source", "") or "")
        if source in {"user_stated", "user_confirmed", "user_corrected"}:
            return False
    return bool(_WEAK_GENRE_TERMS_RE.search(text))
