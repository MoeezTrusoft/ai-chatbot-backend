from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bookcraft.components.attachments.intake import ChatAttachment
from bookcraft.components.context.delegation import SlotResolutionStatus, load_slot_statuses
from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.leads.contact_utils import (
    contact_status_from_dict,
)
from bookcraft.components.trg.schemas import TRGContext
from bookcraft.domain.enums import QueryIntentType, ServiceCategory
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState

if TYPE_CHECKING:
    from bookcraft.components.context.project_manager import ProjectContextSnapshot


class ContextPackBuilder:
    def build(
        self,
        *,
        state: ThreadState,
        intent: IntentVote,
        runtime_atoms: dict[str, Any] | None = None,
        trg_context: TRGContext | None = None,
        project_snapshot: ProjectContextSnapshot | None = None,
        context_enforcement: Any | None = None,
    ) -> ContextPack:
        runtime_atoms = runtime_atoms or {}
        known_facts: list[KnownFact] = []

        project_event = project_snapshot.decision.event if project_snapshot else None

        # For a new-project turn use intent-derived service only; old state facts
        # belong to the previous project and must not bleed into the new scope.
        if project_event == "new_project":
            active_service = (
                intent.service_primary.value if intent.service_primary is not None else None
            )
            active_genre = None
            manuscript_status = None
        else:
            active_service = _active_service(state, intent, runtime_atoms)
            active_genre = _string_field_value(state.project.genre)
            manuscript_status = _string_field_value(state.project.manuscript_status)

        sales_stage = _string_field_value(state.sales_stage)

        if project_event != "new_project":
            _append_field_fact(known_facts, "project.genre", "genre", state.project.genre)
            _append_field_fact(
                known_facts,
                "project.manuscript_status",
                "manuscript_status",
                state.project.manuscript_status,
            )
            _append_field_fact(
                known_facts,
                "project.word_count",
                "word_count",
                state.project.word_count,
            )
            _append_field_fact(
                known_facts,
                "project.page_count",
                "page_count",
                state.project.page_count,
            )
        if active_service is not None:
            _is_new_proj = project_event == "new_project"
            known_facts.append(
                KnownFact(
                    path="service.active",
                    label="active_service",
                    value=active_service,
                    confidence=0.8 if _is_new_proj else _active_service_confidence(state),
                    source="intent" if _is_new_proj else "thread_state",
                    raw_excerpt=None,
                )
            )

        missing_facts = _missing_facts(
            state=state,
            intent=intent,
            active_service=active_service,
            active_genre=active_genre,
            manuscript_status=manuscript_status,
        )
        forbidden_reasks = _forbidden_reasks(
            active_service=active_service,
            active_genre=active_genre,
            manuscript_status=manuscript_status,
        )
        allowed_next_questions = _allowed_next_questions(
            missing_facts=missing_facts,
            active_service=active_service,
        )
        disallowed_next_questions = list(forbidden_reasks)

        outstanding_questions = (
            list(trg_context.outstanding_questions) if trg_context is not None else []
        )
        repeated_user_info = (
            list(trg_context.repeated_user_messages) if trg_context is not None else []
        )
        contradiction_warnings = (
            ["trg_contradiction_warning"]
            if trg_context is not None and trg_context.contradiction_count > 0
            else []
        )

        # Phase 8: merge semantic TRG context into the ContextPack.
        if trg_context is not None:
            # Merge TRG forbidden_reasks (adds facts known from prior turns).
            for label in trg_context.forbidden_reasks:
                if label not in forbidden_reasks:
                    forbidden_reasks.append(label)
            # Surface TRG active_facts as additional known_facts if not in state.
            existing_paths = {kf.path for kf in known_facts}
            for trg_fact in trg_context.active_facts:
                if trg_fact.fact_path not in existing_paths:
                    value = trg_fact.value
                    if not isinstance(value, str | int | float | bool):
                        value = str(value)
                    known_facts.append(
                        KnownFact(
                            path=trg_fact.fact_path,
                            label=trg_fact.fact_path.split(".")[-1],
                            value=value,
                            confidence=trg_fact.confidence,
                            source="trg_semantic",
                            raw_excerpt=trg_fact.raw_excerpt,
                        )
                    )
            # Add contradiction warnings from TRG contradiction events.
            if trg_context.contradictions:
                contradiction_warnings.append(
                    f"trg_semantic_contradiction:{len(trg_context.contradictions)}"
                )

        # Build project memory summary and richer project context.
        project_memory_summary: list[str] = []
        previous_project_summary: list[str] = []
        project_scope_warnings: list[str] = []
        active_project_id: str | None = None
        previous_project_id: str | None = None
        active_project_label: str | None = None
        if project_snapshot is not None:
            active_project_id = project_snapshot.active_project_id
            previous_project_id = project_snapshot.previous_project_id
            # Active project label.
            for proj in project_snapshot.projects:
                if proj.active:
                    active_project_label = proj.label
            # Previous project summaries.
            for proj in project_snapshot.projects:
                if not proj.active:
                    if proj.known_facts:
                        summary = ", ".join(
                            f"{k}={v}" for k, v in list(proj.known_facts.items())[:3]
                        )
                        entry = f"prev_project:{proj.project_id[:8]}:{summary}"
                        project_memory_summary.append(entry)
                        previous_project_summary.append(entry)
                    else:
                        project_memory_summary.append(
                            f"prev_project:{proj.project_id[:8]}:no_facts"
                        )
            # Scope warning when carry_over is not allowed.
            if project_event == "new_project":
                project_scope_warnings.append("carry_over_not_allowed:new_project_active")

        # Apply slot resolution statuses from thread state — project-aware filtering.
        raw_statuses = getattr(state, "slot_resolution_statuses", None) or []
        all_slot_statuses = load_slot_statuses(raw_statuses)

        # Only consider statuses that belong to the active project or have no project_id (legacy).
        slot_statuses = [
            s
            for s in all_slot_statuses
            if s.project_id is None or s.project_id == active_project_id
        ]

        declined_list, delegated_list, unknown_list = _split_slot_statuses(slot_statuses)

        # Slots that are resolved non-positively should not be re-asked.
        resolved_slot_names = {
            s.slot
            for s in slot_statuses
            if s.forbidden_reask
            and s.status in ("delegated", "declined", "unknown_by_user", "not_applicable")
        }
        missing_facts = [f for f in missing_facts if f not in resolved_slot_names]
        allowed_next_questions = [q for q in allowed_next_questions if q not in resolved_slot_names]
        for slot in resolved_slot_names:
            if slot not in forbidden_reasks:
                forbidden_reasks.append(slot)
            if slot not in disallowed_next_questions:
                disallowed_next_questions.append(slot)

        # Portfolio fallback: once fallback_allowed, suppress genre/category re-ask.
        pfs = getattr(state, "portfolio_filter_state", None) or {}
        if pfs.get("fallback_allowed"):
            for _slot in ("genre", "portfolio_filter", "category"):
                if _slot not in forbidden_reasks:
                    forbidden_reasks.append(_slot)
                if _slot not in disallowed_next_questions:
                    disallowed_next_questions.append(_slot)

        # Phase 13: attachment intake fields from state.
        raw_attachments = getattr(state, "attachments_received", None) or []
        attachments_received_list: list[ChatAttachment] = []
        for raw_att in raw_attachments:
            if isinstance(raw_att, dict):
                try:
                    attachments_received_list.append(ChatAttachment.model_validate(raw_att))
                except Exception:  # noqa: BLE001,S110
                    pass
            elif isinstance(raw_att, ChatAttachment):
                attachments_received_list.append(raw_att)

        assessment_type = getattr(state, "latest_assessment_type", None)
        specialist_role = getattr(state, "latest_specialist_role", None)
        lead_objective_stage = getattr(state, "lead_objective_stage", None)

        # PR 3: when attachments are present, suppress scoping slots.
        # The bot must not ask word count, genre, manuscript stage, etc. before handoff.
        if attachments_received_list:
            _att_suppress = {
                "word_or_page_count",
                "word_count",
                "page_count",
                "genre",
                "draft_status",
                "manuscript_stage",
                "manuscript_status",
                "cover_style",
                "deadline",
            }
            missing_facts = [f for f in missing_facts if f not in _att_suppress]
            allowed_next_questions = [q for q in allowed_next_questions if q not in _att_suppress]
            for _slot in _att_suppress:
                if _slot not in forbidden_reasks:
                    forbidden_reasks.append(_slot)
                if _slot not in disallowed_next_questions:
                    disallowed_next_questions.append(_slot)
        lead_created = bool(getattr(state, "lead_created", False))
        contact_info = getattr(state, "contact_info", None) or {}
        # Use sentinel-aware helpers so redacted placeholders never look "ready".
        contact_capture_status = contact_status_from_dict(contact_info)

        # Suppress manuscript_stage re-ask when status is already known.
        if manuscript_status:
            for _ms_slot in ("manuscript_stage", "manuscript_status"):
                if _ms_slot not in forbidden_reasks:
                    forbidden_reasks.append(_ms_slot)
                if _ms_slot not in disallowed_next_questions:
                    disallowed_next_questions.append(_ms_slot)

        # Coherence / assumption-guard fields from runtime atoms and state.
        _genre_status_raw = runtime_atoms.get("genre_status") or getattr(
            state.project, "genre_status", None
        )
        genre_status: str | None = str(_genre_status_raw) if _genre_status_raw else None
        _genre_candidates_raw = runtime_atoms.get("genre_candidates") or getattr(
            state.project, "genre_candidates", None
        )
        genre_candidates: list[str] = list(_genre_candidates_raw) if _genre_candidates_raw else []
        _book_formats_raw = runtime_atoms.get("book_formats") or getattr(
            state.project, "book_formats", None
        )
        book_formats: list[str] = list(_book_formats_raw) if _book_formats_raw else []
        _audience_raw = runtime_atoms.get("audience") or getattr(state.project, "audience", None)
        audience: str | None = str(_audience_raw) if _audience_raw else None
        pending_slots: list[str] = list(getattr(state, "pending_slots", None) or [])
        language_ignored_segments: list[dict[str, str]] = []
        for seg in getattr(state, "language_ignored_segments", None) or []:
            if isinstance(seg, dict):
                language_ignored_segments.append({str(k): str(v) for k, v in seg.items()})

        # Greeting intent guard — suppress scoping when it's a greeting-only turn.
        is_greeting_only = bool(runtime_atoms.get("is_greeting_only"))
        if is_greeting_only:
            for _scope_slot in ("word_or_page_count", "genre", "manuscript_stage", "deadline"):
                if _scope_slot not in forbidden_reasks:
                    forbidden_reasks.append(_scope_slot)
                if _scope_slot not in disallowed_next_questions:
                    disallowed_next_questions.append(_scope_slot)

        # When genre is uncertain, suppress the confirmed genre from known_facts
        # and add genre to missing_facts to prompt clarification.
        if genre_status == "uncertain":
            known_facts = [kf for kf in known_facts if kf.path != "project.genre"]
            if "genre" not in missing_facts:
                missing_facts.append("genre")
            if "genre" in forbidden_reasks:
                forbidden_reasks.remove("genre")
            active_genre = None  # uncertain genre must not surface as active_genre

        # Consultation-first fields (PR 2).
        consultation_stage = getattr(state, "consultation_stage", None)
        current_question_type = getattr(state, "current_question_type", None)
        answer_before_capture_applied = bool(getattr(state, "answer_before_capture_applied", False))
        # preferred_call_time already in ContextPack from PR 1; refresh from state.
        state_preferred_call_time: str | None = getattr(state, "preferred_call_time", None)

        # Suppress scoping slots when contact is ready but call time is missing.
        contact_ready = contact_capture_status == "ready"
        if contact_ready and not state_preferred_call_time:
            for _ct_slot in ("word_or_page_count", "genre", "manuscript_stage", "deadline"):
                if _ct_slot not in forbidden_reasks:
                    forbidden_reasks.append(_ct_slot)
                if _ct_slot not in disallowed_next_questions:
                    disallowed_next_questions.append(_ct_slot)

        # Context enforcement (PR: context-enforcement).
        _enforcement_negated_svcs: list[str] = []
        _enforcement_negated_platforms: list[str] = []
        _enforcement_negated_formats: list[str] = []
        _enforcement_stale_terms: list[str] = []
        _enforcement_warnings: list[str] = []

        if context_enforcement is not None:
            _enf_forbidden = list(getattr(context_enforcement, "forbidden_reasks", None) or [])
            for _ef in _enf_forbidden:
                if _ef not in forbidden_reasks:
                    forbidden_reasks.append(_ef)
                if _ef not in disallowed_next_questions:
                    disallowed_next_questions.append(_ef)
            # Remove enforcement-declared slots from missing_facts and allowed_next_questions.
            _all_enf_slots = set(
                list(getattr(context_enforcement, "delegated_slots", None) or [])
                + list(getattr(context_enforcement, "unknown_slots", None) or [])
                + list(getattr(context_enforcement, "declined_slots", None) or [])
            )
            missing_facts = [f for f in missing_facts if f not in _all_enf_slots]
            allowed_next_questions = [q for q in allowed_next_questions if q not in _all_enf_slots]
            # Clear false facts from known_facts.
            _cleared = set(getattr(context_enforcement, "cleared_false_facts", None) or [])
            if _cleared:
                known_facts = [kf for kf in known_facts if kf.path not in _cleared]
                if "project.genre" in _cleared:
                    active_genre = None
            # Override active_service if enforcement found a replacement.
            _enf_active_svc = getattr(context_enforcement, "active_service", None)
            if _enf_active_svc:
                active_service = str(_enf_active_svc)
            # Collect enforcement metadata for ContextPack fields.
            _enforcement_negated_svcs = list(
                getattr(context_enforcement, "negated_services", None) or []
            )
            _enforcement_negated_platforms = list(
                getattr(context_enforcement, "negated_platforms", None) or []
            )
            _enforcement_negated_formats = list(
                getattr(context_enforcement, "negated_formats", None) or []
            )
            _enforcement_stale_terms = list(
                getattr(context_enforcement, "stale_context_terms", None) or []
            )
            # Suppress stale service from active context.
            _negated_svcs_set = set(_enforcement_negated_svcs)
            if active_service in _negated_svcs_set:
                active_service = _enf_active_svc  # replacement (may still be None)
            known_facts = [
                kf
                for kf in known_facts
                if not (kf.label == "active_service" and kf.value in _negated_svcs_set)
            ]
            _enforcement_warnings = getattr(context_enforcement, "audit", None) or []

        # PR 4: service metadata fields.
        _pub_platforms = list(getattr(state, "publishing_platforms", None) or [])
        _target_retailers = list(getattr(state, "target_retailers", None) or [])
        _isbn_status: str | None = getattr(state, "isbn_status", None)
        _distribution_goal: str | None = getattr(state, "distribution_goal", None)
        _service_metadata: dict[str, dict[str, object]] = dict(
            getattr(state, "service_metadata", None) or {}
        )
        _metadata_candidates: dict[str, list[dict[str, object]]] = dict(
            getattr(state, "metadata_candidates", None) or {}
        )

        # Compute available metadata keys for the active service.
        from bookcraft.components.metadata.service_metadata import (
            get_service_keys,
        )

        _avail_keys: list[str] = get_service_keys(active_service or "") if active_service else []
        # Compute which keys are missing for the active service.
        _confirmed_for_svc = _service_metadata.get(active_service or "", {})
        _missing_meta: list[str] = [k for k in _avail_keys if k not in _confirmed_for_svc]
        # Suppress metadata keys that are already forbidden reasks.
        _missing_meta = [k for k in _missing_meta if k not in set(forbidden_reasks)]

        # Confidence warnings for candidates.
        _confidence_warnings: list[str] = []
        for svc_key, cands in _metadata_candidates.items():
            if cands:
                keys = {c.get("key", "") for c in cands if isinstance(c, dict)}
                for k in keys:
                    _confidence_warnings.append(f"{svc_key}.{k}:uncertain_candidate")

        # Suppress metadata keys that are known — add to forbidden reasks.
        for _known_meta_key in list(_confirmed_for_svc.keys()):
            if _known_meta_key not in forbidden_reasks:
                forbidden_reasks.append(_known_meta_key)

        pack = ContextPack(
            known_facts=known_facts,
            missing_facts=missing_facts,
            forbidden_reasks=forbidden_reasks,
            active_service=active_service,
            active_genre=active_genre,
            manuscript_status=manuscript_status,
            sales_stage=sales_stage,
            outstanding_questions=outstanding_questions,
            repeated_user_info=repeated_user_info,
            contradiction_warnings=contradiction_warnings,
            allowed_next_questions=allowed_next_questions,
            disallowed_next_questions=disallowed_next_questions,
            active_project_id=active_project_id,
            project_event=project_event,
            previous_project_id=previous_project_id,
            project_memory_summary=project_memory_summary,
            active_project_label=active_project_label,
            previous_project_summary=previous_project_summary,
            project_scope_warnings=project_scope_warnings,
            declined_slots=declined_list,
            delegated_slots=delegated_list,
            unknown_slots=unknown_list,
            attachments_received=attachments_received_list,
            assessment_type=assessment_type,
            specialist_role=specialist_role,
            lead_objective_stage=lead_objective_stage,
            contact_capture_status=contact_capture_status,
            lead_created=lead_created,
            genre_status=genre_status,
            genre_candidates=genre_candidates,
            book_formats=book_formats,
            audience=audience,
            pending_slots=pending_slots,
            preferred_call_time=state_preferred_call_time,
            language_ignored_segments=language_ignored_segments,
            is_greeting_turn=is_greeting_only,
            consultation_stage=consultation_stage,
            current_question_type=current_question_type,
            answer_before_capture_applied=answer_before_capture_applied,
            publishing_platforms=_pub_platforms,
            target_retailers=_target_retailers,
            isbn_status=_isbn_status,
            distribution_goal=_distribution_goal,
            service_metadata=_service_metadata,
            metadata_candidates=_metadata_candidates,
            available_service_metadata_keys=_avail_keys,
            metadata_missing_for_active_service=_missing_meta,
            metadata_confidence_warnings=_confidence_warnings,
            negated_services=_enforcement_negated_svcs,
            negated_platforms=_enforcement_negated_platforms,
            negated_formats=_enforcement_negated_formats,
            stale_context_terms=_enforcement_stale_terms,
            context_enforcement_warnings=_enforcement_warnings,
        )
        return pack.model_copy(update={"response_hint": _response_hint(pack)})


def _append_field_fact(
    known_facts: list[KnownFact],
    path: str,
    label: str,
    field: FieldMeta[Any],
) -> None:
    value = _field_value(field)
    if value is None:
        return
    if not isinstance(value, str | int | float | bool):
        value = str(value)
    known_facts.append(
        KnownFact(
            path=path,
            label=label,
            value=value,
            confidence=field.confidence,
            source=field.source.value,
            raw_excerpt=field.raw_excerpt,
        )
    )


def _field_value(field: FieldMeta[Any]) -> str | int | float | bool | None:
    value = field.value
    if value is None:
        return None
    if isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _string_field_value(field: FieldMeta[Any]) -> str | None:
    value = _field_value(field)
    return str(value) if value is not None else None


def _active_service(
    state: ThreadState,
    intent: IntentVote,
    runtime_atoms: dict[str, Any],
) -> str | None:
    if state.project.services_discussed:
        service = state.project.services_discussed[-1].service.value
        if service is None:
            return None
        return service.value if isinstance(service, ServiceCategory) else str(service)
    services = runtime_atoms.get("services")
    if isinstance(services, list):
        for service in services:
            if isinstance(service, str) and service:
                return service
    if intent.service_primary is not None:
        return intent.service_primary.value
    return None


def _active_service_confidence(state: ThreadState) -> float:
    if not state.project.services_discussed:
        return 0.8
    return state.project.services_discussed[-1].confidence


def _missing_facts(
    *,
    state: ThreadState,
    intent: IntentVote,
    active_service: str | None,
    active_genre: str | int | float | bool | None,
    manuscript_status: str | int | float | bool | None,
) -> list[str]:
    missing: list[str] = []
    if state.project.word_count.value is None and state.project.page_count.value is None:
        missing.append("word_or_page_count")
    if active_genre is None:
        missing.append("genre")
    if manuscript_status is None:
        missing.append("manuscript_stage")
    if (
        intent.query_primary
        in {QueryIntentType.PRICING_QUESTION, QueryIntentType.TIMELINE_QUESTION}
        and state.project.target_completion_date.value is None
    ):
        missing.append("deadline")
    if active_service == ServiceCategory.COVER_DESIGN_ILLUSTRATION.value:
        missing.append("cover_style")
    return _ordered_unique(missing)


def _forbidden_reasks(
    *,
    active_service: str | None,
    active_genre: str | int | float | bool | None,
    manuscript_status: str | int | float | bool | None,
) -> list[str]:
    forbidden: list[str] = []
    if active_genre is not None:
        forbidden.extend(["genre", "what genre"])
    if manuscript_status is not None:
        forbidden.extend(["manuscript_stage", "draft status", "starting from scratch"])
    if active_service is not None:
        forbidden.append("unrelated service drift")
    return _ordered_unique(forbidden)


def _allowed_next_questions(
    *,
    missing_facts: list[str],
    active_service: str | None,
) -> list[str]:
    if active_service == ServiceCategory.COVER_DESIGN_ILLUSTRATION.value:
        preferred = ["cover_style", "word_or_page_count", "deadline"]
        return [fact for fact in preferred if fact in missing_facts] + [
            fact for fact in missing_facts if fact not in preferred
        ]
    preferred = ["manuscript_stage", "word_or_page_count", "genre", "deadline"]
    return [fact for fact in preferred if fact in missing_facts] + [
        fact for fact in missing_facts if fact not in preferred
    ]


def _response_hint(pack: ContextPack) -> str | None:
    parts: list[str] = []
    if pack.is_greeting_turn:
        parts.append(
            "This is a greeting-only turn. Welcome the user warmly. "
            "Do NOT ask about genre, word count, manuscript stage, or any scoping detail."
        )
    if pack.genre_status == "uncertain":
        candidates = ", ".join(pack.genre_candidates) if pack.genre_candidates else "unknown"
        parts.append(
            f"Genre is UNCERTAIN — the user mentioned candidates ({candidates}) but has not "
            f"confirmed a genre. Do NOT assert any genre as established. "
            f"Offer options (fiction, memoir/personal story, business/self-help, "
            f"children's book, not sure yet) as a helpful guide."
        )
    if pack.book_formats:
        parts.append(
            f"Book format detected: {', '.join(pack.book_formats)}. "
            f"Treat as format/type, not as a genre. "
            f"{'Audience: ' + pack.audience if pack.audience else 'Audience not yet confirmed'}."
        )
    if pack.known_facts:
        known = ", ".join(f"{fact.label}={fact.value}" for fact in pack.known_facts)
        parts.append(f"Known facts: {known}.")
    if pack.active_service:
        parts.append(f"Active service: {pack.active_service}.")
    if pack.missing_facts:
        parts.append("Missing facts: " + ", ".join(pack.missing_facts) + ".")
    if pack.forbidden_reasks:
        parts.append("Do not ask again for: " + ", ".join(pack.forbidden_reasks) + ".")
    if pack.allowed_next_questions:
        parts.append("Allowed next questions: " + ", ".join(pack.allowed_next_questions) + ".")
    if pack.outstanding_questions:
        parts.append(
            "Previous assistant questions already asked: "
            + " | ".join(pack.outstanding_questions[-3:])
            + "."
        )
    if pack.repeated_user_info:
        parts.append(
            "The user appears to be repeating information; acknowledge it and move forward."
        )
    if pack.contradiction_warnings:
        parts.append("There may be contradictory project details; ask one focused question.")
    if pack.language_ignored_segments:
        parts.append(
            "Some non-English segments were ignored. Answer the English portion only. "
            "Ask the user to continue in English through one gentle prompt."
        )
    # Consultation-first guidance.
    if pack.consultation_stage == "consultation_pending":
        parts.append(
            "CONSULTATION IS PENDING. Do NOT ask for word count, genre, deadline, or any "
            "scoping detail. Confirm specialist follow-up and timing only."
        )
    elif pack.consultation_stage == "consultation_time_requested":
        parts.append(
            "Contact is captured. Ask for the user's preferred call time only. "
            "Do NOT ask for word count, genre, or deadline."
        )
    if pack.current_question_type:
        parts.append(
            f"User asked a priority question ({pack.current_question_type}). "
            f"Answer this concern first before asking for contact details."
        )
    if pack.answer_before_capture_applied:
        parts.append(
            "Answer-before-capture policy was applied this turn. "
            "Do not open with a contact request — answer the concern first."
        )
    if pack.preferred_call_time and pack.lead_created:
        parts.append(
            f"Preferred call time captured: {pack.preferred_call_time}. Confirm specialist handoff."
        )
    return " ".join(parts) if parts else None


def _ordered_unique(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def _split_slot_statuses(
    statuses: list[SlotResolutionStatus],
) -> tuple[list[SlotResolutionStatus], list[SlotResolutionStatus], list[SlotResolutionStatus]]:
    declined = [s for s in statuses if s.status == "declined"]
    delegated = [s for s in statuses if s.status in ("delegated", "not_applicable")]
    unknown = [s for s in statuses if s.status == "unknown_by_user"]
    return declined, delegated, unknown
