from __future__ import annotations

import re
from typing import Any

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.state import ThreadState

YES_CONFIRMATIONS = {
    "yes",
    "yes please",
    "yeah",
    "yep",
    "sure",
    "ok",
    "okay",
    "confirm",
    "confirmed",
    "book it",
    "go ahead",
    "that works",
    "sounds good",
    "please do",
}


TIME_HINT_RE = re.compile(
    r"\b("
    r"today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}\s*(?:am|pm)|morning|afternoon|evening|next week|this week"
    r")\b",
    flags=re.IGNORECASE,
)

DATE_HINT_RE = re.compile(
    r"\b("
    r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    r")",
    flags=re.IGNORECASE,
)


def is_confirmation_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().casefold())
    normalized = normalized.strip(".! ")

    if normalized in YES_CONFIRMATIONS:
        return True

    affirmative_starts = (
        "yes ",
        "yes,",
        "yes please",
        "yeah ",
        "yeah,",
        "yep ",
        "yep,",
        "sure ",
        "sure,",
        "ok ",
        "okay ",
    )
    confirmation_actions = (
        "send it",
        "send",
        "book it",
        "book",
        "confirm",
        "go ahead",
        "please do",
        "do it",
    )

    return normalized.startswith(affirmative_starts) and any(
        action in normalized for action in confirmation_actions
    )


def field_value(value: object) -> object | None:
    return getattr(value, "value", None)


def first_runtime_value(
    runtime_atoms: dict[str, object],
    key: str,
) -> str | None:
    values = runtime_atoms.get(key)
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    return first if isinstance(first, str) else str(first)


def contact_slots(
    *,
    state: ThreadState,
    extraction: CombinedExtraction,
    processed: ProcessedMessage,
) -> dict[str, str]:
    atoms = processed.deterministic_atoms
    slots: dict[str, str] = {}

    name = extraction.contact.full_name or field_value(state.personal.name)
    email = (
        extraction.contact.email
        or first_runtime_value(atoms, "emails")
        or field_value(state.personal.email)
    )
    phone = (
        extraction.contact.phone
        or first_runtime_value(atoms, "phones")
        or field_value(state.personal.phone)
    )

    if isinstance(name, str) and name.strip():
        slots["name"] = name.strip()
    if isinstance(email, str) and email.strip():
        slots["email"] = email.strip()
    if isinstance(phone, str) and phone.strip():
        slots["phone"] = phone.strip()

    return slots


def project_slots(
    *,
    state: ThreadState,
    extraction: CombinedExtraction,
    processed: ProcessedMessage,
) -> dict[str, Any]:
    atoms = processed.deterministic_atoms
    slots: dict[str, Any] = {}

    word_count = extraction.project.word_count or field_value(state.project.word_count)
    page_count = extraction.project.page_count or field_value(state.project.page_count)
    genre = extraction.project.genre or field_value(state.project.genre)
    manuscript_status = extraction.project.manuscript_status or field_value(
        state.project.manuscript_status
    )
    deadline = extraction.project.target_launch_window or field_value(
        state.project.target_completion_date
    )

    runtime_word_counts = atoms.get("word_counts")
    runtime_page_counts = atoms.get("page_counts")

    if word_count is None and isinstance(runtime_word_counts, list) and runtime_word_counts:
        word_count = runtime_word_counts[0]
    if page_count is None and isinstance(runtime_page_counts, list) and runtime_page_counts:
        page_count = runtime_page_counts[0]

    if word_count is not None:
        slots["word_count"] = word_count
    if page_count is not None:
        slots["page_count"] = page_count
    if genre is not None:
        slots["genre"] = str(genre)
    if manuscript_status is not None:
        slots["manuscript_status"] = str(manuscript_status)
    if deadline is not None:
        slots["deadline"] = str(deadline)

    return slots


def service_values(
    *,
    intent: IntentVote,
    processed: ProcessedMessage,
) -> list[str]:
    values: list[str] = []

    runtime_services = processed.deterministic_atoms.get("services")
    if isinstance(runtime_services, list):
        values.extend(str(item) for item in runtime_services)

    if intent.service_primary is not None:
        values.append(intent.service_primary.value)

    values.extend(service.value for service in intent.service_secondary)

    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)

    return ordered


def has_time_hint(text: str) -> bool:
    return bool(TIME_HINT_RE.search(text) or DATE_HINT_RE.search(text))


def lead_follow_up_slots(contact: dict[str, str]) -> list[str]:
    follow_up: list[str] = []
    if "name" not in contact:
        follow_up.append("name")
    if "email" not in contact:
        follow_up.append("email")
    if "phone" not in contact:
        follow_up.append("phone")
    return follow_up


def has_email_or_phone(contact: dict[str, str]) -> bool:
    return bool(contact.get("email") or contact.get("phone"))
