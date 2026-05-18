from __future__ import annotations

from typing import Any

from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.trg.schemas import TRGContext
from bookcraft.domain.enums import QueryIntentType, ServiceCategory
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


class ContextPackBuilder:
    def build(
        self,
        *,
        state: ThreadState,
        intent: IntentVote,
        runtime_atoms: dict[str, Any] | None = None,
        trg_context: TRGContext | None = None,
    ) -> ContextPack:
        runtime_atoms = runtime_atoms or {}
        known_facts: list[KnownFact] = []

        active_service = _active_service(state, intent, runtime_atoms)
        active_genre = _string_field_value(state.project.genre)
        manuscript_status = _string_field_value(state.project.manuscript_status)
        sales_stage = _string_field_value(state.sales_stage)

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
            known_facts.append(
                KnownFact(
                    path="service.active",
                    label="active_service",
                    value=active_service,
                    confidence=_active_service_confidence(state),
                    source="thread_state",
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
