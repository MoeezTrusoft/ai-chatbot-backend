from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.leads.contact_utils import is_real_contact_value
from bookcraft.components.preprocessor.detectors.date_hint_detector import DATE_HINT_RE
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.state import ThreadState

YES_CONFIRMATIONS = {
    "yes",
    "yes please",
    "confirm",
    "confirmed",
    "confirm booking",
    "confirm the booking",
    "book it",
    "yes book it",
    "yes schedule the consultation",
    "go ahead and schedule it",
    "yes send it",
}

# Action-agnostic affirmatives accepted for ANY known pending action. They contain
# no action-specific verb, so they cannot cross-confirm the wrong action (the caller
# already knows what is pending). This is what a customer actually types to confirm.
_GENERIC_POSITIVES = frozenset(
    {
        "yes", "yes please", "yep", "yeah", "yup", "sure", "ok", "okay",
        "confirm", "confirmed", "confirm it", "please confirm",
        "go ahead", "yes go ahead", "please go ahead", "go for it",
        "proceed", "yes proceed", "please proceed", "let's proceed",
        "do it", "yes do it", "please do", "do that", "yes do that",
        "send it", "yes send it", "send it over", "please send it",
        "sounds good", "that works", "yes that works", "works for me",
        "sure thing", "absolutely", "definitely", "yes lets do it", "lets do it",
    }
)

# ---------------------------------------------------------------------------
# Pending confirmation TTLs (seconds)
# ---------------------------------------------------------------------------

_CONFIRMATION_TTL: dict[str, int] = {
    "generate_nda": 1800,  # 30 min
    "generate_agreement": 1800,  # 30 min
    "schedule_consultation": 3600,  # 60 min
    "create_lead": 3600,  # 60 min
    "price_quote": 3600,  # 60 min
}
_DEFAULT_TTL = 3600

# ---------------------------------------------------------------------------
# Action-type → confirmation keywords
# ---------------------------------------------------------------------------


def _c(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.I)


_ACTION_CONFIRMATION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "schedule_consultation": (
        _c(r"\b(?:book|schedule|confirm\s+(?:the\s+)?(?:call|consultation|booking))\b"),
        _c(r"\b(?:yes\s+(?:book|schedule)|tomorrow\s+(?:works?|morning|afternoon|evening))\b"),
    ),
    "generate_nda": (
        _c(
            r"\b(?:send\s+(?:the\s+)?nda|confirm\s+(?:the\s+)?nda"
            r"|yes\s+(?:send|generate)\s+(?:the\s+)?nda)\b"
        ),
    ),
    "generate_agreement": (
        _c(
            r"\b(?:send\s+(?:the\s+)?agreement|approve\s+(?:the\s+)?agreement"
            r"|confirm\s+(?:the\s+)?agreement)\b"
        ),
    ),
    "price_quote": (
        _c(
            r"\b(?:yes\s+(?:use\s+those|generate\s+(?:the\s+)?estimate|proceed)"
            r"|approve\s+(?:the\s+)?(?:quote|estimate))\b"
        ),
    ),
    "create_lead": (_c(r"\b(?:yes|confirm|proceed|go\s+ahead)\b"),),
}


TIME_HINT_RE = re.compile(
    r"\b("
    r"today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}\s*(?:am|pm)|morning|afternoon|evening|next week|this week"
    r")\b",
    flags=re.IGNORECASE,
)


def _normalize_confirmation(text: str) -> str:
    n = re.sub(r"\s+", " ", text.strip().casefold())
    n = n.strip(".! ")
    n = n.replace(",", " ")
    return re.sub(r"\s+", " ", n).strip()


def is_confirmation_text(text: str, pending_action_type: str | None = None) -> bool:
    """Return True when the text is a valid confirmation for the pending action.

    When *pending_action_type* is given, only accept phrases that are
    semantically appropriate for that action type — preventing cross-action
    confirmation (e.g. "schedule it" confirming an NDA).

    If *pending_action_type* is None the legacy broad check is used.
    """
    normalized = _normalize_confirmation(text)

    non_confirmation_patterns = (
        r"\bsend\s+me\s+(?:pricing|price|samples?|portfolio|nda|agreement|more)\b",
        r"\byes\s+send\s+more\s+info\b",
        r"\bi\s+want\s+to\s+know\s+more\s+before\s+booking\b",
        r"\bcan\s+you\s+send\s+(?:the\s+)?(?:samples?|nda|agreement)\b",
    )
    if any(re.search(pattern, normalized) for pattern in non_confirmation_patterns):
        return False

    # --- Action-specific check (Batch 1 Step 5) ---
    if pending_action_type is not None:
        # Truly generic positive words are always acceptable — they carry no
        # cross-action ambiguity (e.g. "yes" can never mean "book a call"
        # when pending action is NDA, but it also cannot accidentally trigger
        # the wrong action because the CALLER already knows what is pending).
        # Truly action-agnostic affirmatives. These carry no action-specific verb
        # (unlike "schedule it" / "send the nda"), so they cannot cross-confirm the
        # wrong action — the caller already knows which action is pending. Kept broad
        # on purpose: a customer confirming an NDA naturally says "yes, send it" or
        # "go ahead", not "yes generate the nda" (chat regression — those replies were
        # silently dropped as non-confirmations and the action never fired).
        if normalized in _GENERIC_POSITIVES:
            return True
        specific = _ACTION_CONFIRMATION_PATTERNS.get(pending_action_type)
        if specific:
            return any(p.search(normalized) for p in specific)
        # Unknown action type: fall through to broad check.

    # --- Legacy broad check (preserved for backward compat) ---
    if normalized in YES_CONFIRMATIONS:
        return True

    confirmation_patterns = (
        r"\byes\s+(?:please\s+)?book\s+it\b",
        r"\byes\s+(?:please\s+)?(?:schedule|confirm)\s+(?:it|the consultation|booking)\b",
        r"\bschedule\s+it\b",
        r"\bconfirm\s+(?:it|the booking|booking|the consultation)\b",
        r"\bgo\s+ahead\s+and\s+schedule\s+(?:it|the consultation)\b",
    )
    return any(re.search(pattern, normalized) for pattern in confirmation_patterns)


def is_pending_expired(pending: Any, now: datetime | None = None) -> bool:
    """Return True when the pending confirmation has passed its expiry time."""
    if pending is None:
        return False
    expires_at = getattr(pending, "expires_at", None)
    if expires_at is None:
        return False
    now = now or datetime.now(UTC)
    # Ensure both are tz-aware for comparison.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return bool(now >= expires_at)


def pending_ttl_seconds(action_type: str) -> int:
    """Return the TTL in seconds for a given pending action type."""
    return _CONFIRMATION_TTL.get(action_type, _DEFAULT_TTL)


def make_pending_expires_at(action_type: str, now: datetime | None = None) -> datetime:
    """Return the datetime at which a new pending confirmation should expire."""
    now = now or datetime.now(UTC)
    return now + timedelta(seconds=pending_ttl_seconds(action_type))


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


def _first_real_contact_value(*values: object) -> str | None:
    """Return the first genuinely user-provided contact value, skipping redacted sentinels."""
    for v in values:
        if isinstance(v, str) and v.strip() and not v.startswith("[REDACTED_"):
            stripped = v.strip()
            if stripped and is_real_contact_value(stripped):
                return stripped
    return None


def contact_slots(
    *,
    state: ThreadState,
    extraction: CombinedExtraction,
    processed: ProcessedMessage,
) -> dict[str, str]:
    """Build contact slot dict from all canonical contact sources in priority order.

    Phase 3 fix: read from state.contact_info (the primary durable store) so that
    contact captured in a previous turn is available to the action planner even when
    extraction and runtime atoms are empty.

    Priority (highest → lowest):
      1. Current-turn extraction (CombinedExtraction)
      2. Current-turn runtime atoms (deterministic pre-processor)
      3. state.contact_info (synced by ChatService from ContactCaptureDetector)
      4. state.sales_actions.lead (set after successful lead creation)
      5. state.personal FieldMeta (set by state_applier from extraction deltas)
    """
    atoms = processed.deterministic_atoms
    slots: dict[str, str] = {}

    # Gather all candidate sources.
    ci = getattr(state, "contact_info", None) or {}
    lead = state.sales_actions.lead

    name = _first_real_contact_value(
        extraction.contact.full_name,
        _extract_contact_name(processed.raw or ""),
        ci.get("name"),
        lead.name,
        field_value(state.personal.name),
    )
    email = _first_real_contact_value(
        extraction.contact.email,
        first_runtime_value(atoms, "emails"),
        ci.get("email"),
        lead.email,
        field_value(state.personal.email),
    )
    phone = _first_real_contact_value(
        extraction.contact.phone,
        first_runtime_value(atoms, "phones"),
        ci.get("phone"),
        lead.phone,
        field_value(state.personal.phone),
    )

    if name:
        slots["name"] = name
    if email:
        slots["email"] = email
    if phone:
        slots["phone"] = phone

    return slots


def contact_ready_from_slots(slots: dict[str, str]) -> bool:
    """Return True when slots contain name + (email OR phone)."""
    return bool(slots.get("name")) and bool(slots.get("email") or slots.get("phone"))


def has_real_email_or_phone(contact: dict[str, Any]) -> bool:
    """Return True when contact dict has at least one real email or phone."""
    return _first_real_contact_value(contact.get("email"), contact.get("phone")) is not None


def _extract_contact_name(text: str) -> str | None:
    patterns = [
        r"\bmy name is\s+([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,4})\b",
        r"\bi am\s+([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,4})"
        r"(?=,\s*(?:my\s+)?(?:email|phone|number)\b)",
        r"\bthis is\s+([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,4})(?=,|\.|\band\b|$)",
        r"\b([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,4})\s+here\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue

        candidate = _clean_name_candidate(match.group(1))
        if candidate and _looks_like_person_name(candidate):
            return candidate

    return None


def _name_from_text(text: str) -> str | None:
    return _extract_contact_name(text)


def _clean_name_candidate(value: str) -> str | None:
    candidate = value.strip(" ,.;:-")

    # Stop names before contact-info phrases.
    # Example: "Maya Author and my email is maya@example.com" -> "Maya Author"
    candidate = re.split(
        r"\band\s+(?:my\s+)?(?:email|phone|number)\b",
        candidate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    candidate = re.split(
        r"\b(?:my\s+)?(?:email|phone|number)\b",
        candidate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]

    candidate = re.split(r"\b[\w.+-]+@[\w.-]+\b", candidate)[0]
    candidate = re.split(r"\+?\d[\d\s().-]{4,}", candidate)[0]
    candidate = candidate.strip(" ,.;:-")

    if _is_rejected_name_phrase(candidate):
        return None

    words = candidate.split()
    if not 1 <= len(words) <= 5:
        return None

    if not all(re.match(r"^[A-Za-z][A-Za-z'.-]*$", word) for word in words):
        return None

    return " ".join(word[:1].upper() + word[1:] for word in words)


def _looks_like_person_name(value: str) -> bool:
    words = value.split()
    if not 1 <= len(words) <= 4:
        return False
    return all(re.match(r"^[A-Z][A-Za-z'.-]*$", word) for word in words)


def _is_rejected_name_phrase(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
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
        "writing",
        "working",
        "looking",
        "self-publishing",
        "self publishing",
        "a memoir",
        "a professional editor",
        "pricing",
    }

    if normalized in blocked:
        return True
    if re.match(r"^(?:a|an|the|my)\s+\w+", normalized):
        return True
    return any(
        phrase in normalized
        for phrase in (
            "writing a",
            "working on",
            "looking for",
            "self-publishing",
            "self publishing",
            "happy to proceed",
            "professional editor",
        )
    )


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
    # Use sentinel-aware check so redacted placeholders don't count as real contact.
    return is_real_contact_value(contact.get("email")) or is_real_contact_value(
        contact.get("phone")
    )


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
    return _extract_firm_deadline(text)


def _extract_firm_deadline(text: str) -> str | None:
    patterns = [
        r"\bdeadline\s+(?:is|:)?\s+(.+?)(?=,|\.|\band\b|$)",
        r"\bby\s+((?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?)\b",
        r"\bready\s+in\s+(?:about\s+)?(\d+\s+(?:day|days|week|weeks|month|months))\b",
        r"\bin\s+(?:about\s+)?(\d+\s+(?:day|days|week|weeks|month|months))\b",
        r"\bwithin\s+(\d+\s+(?:day|days|week|weeks|month|months))\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" ,.;:-")
            if _is_vague_deadline(candidate):
                continue
            return candidate

    return None


def _is_vague_deadline(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
    vague_phrases = {
        "whenever",
        "soon",
        "when ready",
        "later",
        "not sure",
        "no rush",
        "flexible",
    }
    return any(phrase in normalized for phrase in vague_phrases)
