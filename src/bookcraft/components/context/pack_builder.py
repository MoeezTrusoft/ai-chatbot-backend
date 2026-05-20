from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bookcraft.components.context.delegation import SlotResolutionStatus, load_slot_statuses
from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.intent.schemas import IntentVote
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
