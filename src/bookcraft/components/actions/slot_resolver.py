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

    name = (
        extraction.contact.full_name
        or field_value(state.personal.name)
        or _name_from_text(processed.raw or "")
    )
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


def _name_from_text(text: str) -> str | None:
    patterns = [
        r"\bmy name is\s+([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,4})\b",
        r"\bi am\s+([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,4})(?=,|\.|\band\b|$)",
        r"\bthis is\s+([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,4})(?=,|\.|\band\b|$)",
        r"\buse\s+([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,4})(?=,|\.|\band\b|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue

        candidate = _clean_name_candidate(match.group(1))
        if candidate:
            return candidate

    return None


def _clean_name_candidate(value: str) -> str | None:
    candidate = value.strip(" ,.;:-")

    candidate = re.split(r"\b[\w.+-]+@[\w.-]+\b", candidate)[0]
    candidate = re.split(r"\+?\d[\d\s().-]{4,}", candidate)[0]
    candidate = candidate.strip(" ,.;:-")

    blocked = {
        "me",
        "my email",
        "email",
        "phone",
        "tomorrow",
        "today",
        "consultation",
        "service agreement",
        "nda",
        "houston",
        "texas",
        "tx",
    }

    if candidate.casefold() in blocked:
        return None

    words = candidate.split()
    if not 1 <= len(words) <= 5:
        return None

    if not all(re.match(r"^[A-Za-z][A-Za-z'.-]*$", word) for word in words):
        return None

    return " ".join(word[:1].upper() + word[1:] for word in words)


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

    if word_count is None:
        word_count = _word_count_from_text(processed.raw)
    if page_count is None:
        page_count = _page_count_from_text(processed.raw)
    if genre is None:
        genre = _genre_from_text(processed.raw)
    if manuscript_status is None:
        manuscript_status = _manuscript_status_from_text(processed.raw)
    if deadline is None:
        deadline = _deadline_from_text(processed.raw)

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


def _word_count_from_text(text: str) -> int | None:
    patterns = [
        r"\bword\s+count\s+(?:is|:)?\s*(\d{1,3}(?:,\d{3})+|\d{4,7})\b",
        r"\b(\d{1,3}(?:,\d{3})+|\d{4,7})\s*[- ]?word\b",
        r"\b(\d{1,3}(?:,\d{3})+|\d{4,7})\s+words\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))

    return None


def _page_count_from_text(text: str) -> int | None:
    patterns = [
        r"\bpage\s+count\s+(?:is|:)?\s*(\d{1,4})\b",
        r"\b(\d{1,4})\s*[- ]?page\b",
        r"\b(\d{1,4})\s+pages\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def _genre_from_text(text: str) -> str | None:
    compact_match = re.search(
        r"\b\d{1,3}(?:,\d{3})*\s*[- ]?word\s+"
        r"([A-Za-z][A-Za-z /&-]{1,40})\s+(?:draft|manuscript)\b",
        text,
        flags=re.IGNORECASE,
    )
    if compact_match:
        candidate = _clean_genre_candidate(compact_match.group(1))
        if candidate is not None:
            return candidate

    patterns = [
        r"\bgenre\s+(?:is|:)?\s+([A-Za-z][A-Za-z /&-]{1,60})(?=,|\.|\band\b|$)",
        r"\b([A-Za-z][A-Za-z /&-]{1,40})\s+genre\b",
        r"\b(\w+(?:\s+\w+)?)\s+(?:draft|manuscript)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue

        candidate = _clean_genre_candidate(match.group(1))
        if candidate is None:
            continue
        return candidate

    return None


def _clean_genre_candidate(value: str) -> str | None:
    candidate = value.strip(" ,.;:-")
    candidate = re.sub(r"^word\s+", "", candidate, flags=re.IGNORECASE).strip()
    candidate = re.sub(r"^page\s+", "", candidate, flags=re.IGNORECASE).strip()

    blocked = {
        "complete",
        "partial",
        "rough",
        "final",
        "full",
        "draft",
        "manuscript",
        "word",
        "page",
    }
    if candidate.casefold() in blocked:
        return None

    if not 2 <= len(candidate) <= 60:
        return None

    return candidate


def _manuscript_status_from_text(text: str) -> str | None:
    lowered = text.casefold()

    status_phrases = [
        ("complete draft", "complete draft"),
        ("completed draft", "complete draft"),
        ("complete manuscript", "complete manuscript"),
        ("full draft", "complete draft"),
        ("rough draft", "rough draft"),
        ("first draft", "first draft"),
        ("partial draft", "partial draft"),
        ("outline", "outline"),
        ("idea stage", "idea stage"),
    ]

    for phrase, value in status_phrases:
        if phrase in lowered:
            return value

    match = re.search(
        r"\bmanuscript\s+status\s+(?:is|:)?\s+(.+?)(?=,|\.|\band\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip(" ,.;:-")

    return None


def _deadline_from_text(text: str) -> str | None:
    patterns = [
        r"\bdeadline\s+(?:is|:)?\s+(.+?)(?=,|\.|\band\b|$)",
        r"\bready\s+in\s+(?:about\s+)?(\d+\s+(?:day|days|week|weeks|month|months))\b",
        r"\bin\s+(?:about\s+)?(\d+\s+(?:day|days|week|weeks|month|months))\b",
        r"\bwithin\s+(\d+\s+(?:day|days|week|weeks|month|months))\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" ,.;:-")

    return None
