import re
from dataclasses import dataclass

from prometheus_client import Counter, Histogram

from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.leads.contact_utils import is_valid_phone
from bookcraft.components.preprocessor.detectors.document_request_detector import (
    has_agreement_request,
    has_nda_request,
)
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import Source
from bookcraft.domain.state import ThreadState

# ---------------------------------------------------------------------------
# Step 1: Deterministic consultation extraction
# ---------------------------------------------------------------------------

_CONSULTATION_RE = re.compile(
    r"\b(?:free\s+consultation|book\s+(?:a\s+)?call|schedule\s+(?:a\s+)?call|"
    r"talk\s+to\s+(?:a\s+)?(?:someone|specialist|expert|person|agent|team)|"
    r"speak\s+(?:with|to)\s+(?:a\s+)?(?:someone|specialist|expert|person)|"
    r"call\s+me(?:\s+(?:back|tomorrow|today|at))?|callback|consultant|consultation|"
    r"can\s+someone\s+(?:contact|call|reach)\s+me|"
    r"let'?s\s+(?:schedule|book|set\s+up|arrange)|"
    r"book\s+me\s+in|set\s+up\s+a\s+(?:call|meeting|appointment)|"
    r"talk\s+to\s+your\s+team|connect\s+(?:me\s+)?with\s+(?:a\s+)?specialist|"
    r"works?\s+for\s+a\s+(?:call|meeting)|schedule\s+it)\b",
    re.IGNORECASE,
)

# Extract date phrase (longest match preferred)
_DATE_PHRASE_RE = re.compile(
    r"\b(?:next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week)|"
    r"this\s+(?:monday|tuesday|wednesday|thursday|friday|weekend|week)|"
    r"tomorrow|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

# Extract time phrase
_TIME_PHRASE_RE = re.compile(
    r"\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|morning|afternoon|evening|noon|midnight|"
    r"after\s+\d{1,2}(?::\d{2})?|around\s+\d{1,2}(?::\d{2})?(?:\s*(?:am|pm))?)\b",
    re.IGNORECASE,
)

# Timezone patterns
_TIMEZONE_RE = re.compile(
    r"\b(?:PST|PDT|MST|MDT|CST|CDT|EST|EDT|GMT|UTC|PKT|IST|BST|CET|CEST|"
    r"(?:Pacific|Mountain|Central|Eastern|Pakistan|India|British|European)\s+(?:time|timezone|standard\s+time))\b",
    re.IGNORECASE,
)

# Channel preference
_CHANNEL_RE = re.compile(
    r"\b(?:phone|email|zoom|google\s+meet|whatsapp|telegram|skype|teams|video\s+call|"
    r"audio\s+call|in\s+person)\b",
    re.IGNORECASE,
)


def extract_consultation(text: str, known_timezone: str | None = None) -> dict[str, object]:
    """Return a dict of consultation extraction fields, or empty dict if not requested."""
    if not _CONSULTATION_RE.search(text):
        return {}

    date_m = _DATE_PHRASE_RE.search(text)
    time_m = _TIME_PHRASE_RE.search(text)
    tz_m = _TIMEZONE_RE.search(text)
    chan_m = _CHANNEL_RE.search(text)

    date_text = date_m.group(0) if date_m else None
    time_text = time_m.group(0) if time_m else None
    tz_text = tz_m.group(0) if tz_m else None

    # Build combined datetime phrase — prefer the longest span covering both.
    datetime_text: str | None = None
    if date_text and time_text:
        # Try to extract a combined phrase from the original text.
        combined_re = re.compile(
            rf"{re.escape(date_text)}.{{0,20}}{re.escape(time_text)}|"
            rf"{re.escape(time_text)}.{{0,20}}{re.escape(date_text)}",
            re.IGNORECASE,
        )
        m = combined_re.search(text)
        datetime_text = m.group(0).strip() if m else f"{date_text} {time_text}"
    elif date_text:
        datetime_text = date_text
    elif time_text:
        datetime_text = time_text

    # Timezone unknown when relative time given but no tz detected or known.
    timezone_unknown = bool((date_text or time_text) and not tz_text and not known_timezone)

    return {
        "requested": True,
        "requested_date_text": date_text,
        "requested_time_text": time_text,
        "requested_datetime_text": datetime_text,
        "timezone_text": tz_text,
        "channel_preference": chan_m.group(0).lower() if chan_m else None,
        "timezone_unknown": timezone_unknown,
    }


EXTRACTION_SECONDS = Histogram("extraction_seconds", "Combined extraction latency.")
EXTRACTION_FIELDS = Counter(
    "extraction_fields_per_turn",
    "Fields extracted per turn.",
    ["category"],
)

# ---------------------------------------------------------------------------
# Step 4: Correction-phrase detection
# ---------------------------------------------------------------------------

_CORRECTION_RE = re.compile(
    r"\b(?:actually|correction|i\s+meant|not\s+\w+[,;]\s*(?:it'?s?\s+)?|"
    r"it\s+is\s+not|change\s+it\s+to|i\s+decided|now\s+it'?s?\s+|"
    r"definitely|it'?s?\s+actually|wait[,\s]+(?:no|it'?s?\s+)|"
    r"i\s+was\s+wrong|let\s+me\s+correct)\b",
    re.IGNORECASE,
)


def is_correction_turn(text: str) -> bool:
    """Return True when the message contains an explicit correction signal."""
    return bool(_CORRECTION_RE.search(text))


@dataclass(slots=True)
class CombinedExtractor:
    provider_name: str = "mock_haiku"

    async def extract(self, message: ProcessedMessage, state: ThreadState) -> CombinedExtraction:
        with EXTRACTION_SECONDS.time():
            extraction = CombinedExtraction()
            atoms = message.deterministic_atoms
            if emails := atoms.get("emails"):
                email = _first_string(emails)
                if email:
                    extraction.contact.email = email
                    extraction.state_deltas.append(
                        StateDelta(
                            path="personal.email",
                            value=email,
                            confidence=0.98,
                            source=Source.USER_STATED,
                            extracted_by="deterministic_preextractor.v1",
                            raw_excerpt=email,
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="contact").inc()
            if phones := atoms.get("phones"):
                phone = _first_string(phones)
                # Guard: reject year/era ranges ("1770-1810"), age ranges ("6-12"),
                # and anything without 10–15 digits before it becomes personal.phone.
                if phone and is_valid_phone(phone):
                    extraction.contact.phone = phone
                    extraction.state_deltas.append(
                        StateDelta(
                            path="personal.phone",
                            value=phone,
                            confidence=0.92,
                            source=Source.USER_STATED,
                            extracted_by="deterministic_preextractor.v1",
                            raw_excerpt=phone,
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="contact").inc()
            # A count stated on a correction turn ("actually 80k instead") must be
            # allowed to overwrite an existing count — tag it USER_CORRECTED so the
            # state applier bypasses the confidence gate (otherwise the bot says the
            # new number but keeps storing the old one).
            _count_source = (
                Source.USER_CORRECTED if is_correction_turn(message.raw) else Source.USER_STATED
            )
            if word_counts := atoms.get("word_counts"):
                count = _first_int(word_counts)
                if count is not None:
                    extraction.project.word_count = count
                    extraction.state_deltas.append(
                        StateDelta(
                            path="project.word_count",
                            value=count,
                            confidence=0.96,
                            source=_count_source,
                            extracted_by="deterministic_preextractor.v1",
                            raw_excerpt=f"{count} words",
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="project").inc()
            if page_counts := atoms.get("page_counts"):
                count = _first_int(page_counts)
                if count is not None:
                    extraction.project.page_count = count
                    extraction.state_deltas.append(
                        StateDelta(
                            path="project.page_count",
                            value=count,
                            confidence=0.94,
                            source=_count_source,
                            extracted_by="deterministic_preextractor.v1",
                            raw_excerpt=f"{count} pages",
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="project").inc()

            # Context-aware bare number extraction: when the bot just asked for
            # word/page count and the user replies with only numbers ("200 1000"
            # or "3000"), extract the largest as word_count (and optionally a
            # smaller one as page_count).  Only activates when no labelled counts
            # were found above and the state shows a length question was pending.
            if (
                not extraction.project.word_count
                and not extraction.project.page_count
                and state is not None
                and _state_asked_for_length(state)
            ):
                _bare = _extract_bare_length_numbers(message.raw or message.normalized or "")
                if _bare:
                    _wc = max(_bare)  # treat the largest number as word count
                    extraction.project.word_count = _wc
                    extraction.state_deltas.append(
                        StateDelta(
                            path="project.word_count",
                            value=_wc,
                            confidence=0.82,
                            source=Source.USER_STATED,
                            extracted_by="context_bare_number.v1",
                            raw_excerpt=str(_wc),
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="project").inc()
                    # If two numbers provided (e.g. "200 1000"), treat the smaller
                    # as page count only when it's plausibly a page count (≤ 2000).
                    if len(_bare) >= 2:
                        _pc = min(_bare)
                        if 1 <= _pc <= 2000:
                            extraction.project.page_count = _pc
                            extraction.state_deltas.append(
                                StateDelta(
                                    path="project.page_count",
                                    value=_pc,
                                    confidence=0.75,
                                    source=Source.USER_STATED,
                                    extracted_by="context_bare_number.v1",
                                    raw_excerpt=str(_pc),
                                )
                            )
                            EXTRACTION_FIELDS.labels(category="project").inc()

            if status := atoms.get("manuscript_status"):
                extraction.project.manuscript_status = str(status)
                extraction.state_deltas.append(
                    StateDelta(
                        path="project.manuscript_status",
                        value=status,
                        confidence=0.86,
                        source=Source.USER_STATED,
                        extracted_by="deterministic_preextractor.v1",
                        raw_excerpt=str(status),
                    )
                )
                EXTRACTION_FIELDS.labels(category="project").inc()
            if genre := atoms.get("genre"):
                genre_text = str(genre)
                extraction.project.genre = genre_text
                # Step 4/5: if message contains a correction phrase, mark as USER_CORRECTED
                # so state applier allows it to override equal-confidence existing values.
                _genre_source = (
                    Source.USER_CORRECTED if is_correction_turn(message.raw) else Source.USER_STATED
                )
                extraction.state_deltas.append(
                    StateDelta(
                        path="project.genre",
                        value=genre_text,
                        confidence=0.9,
                        source=_genre_source,
                        extracted_by="deterministic_preextractor.v1",
                        raw_excerpt=genre_text,
                    )
                )
                EXTRACTION_FIELDS.labels(category="project").inc()
            if services := _string_list(atoms.get("services")):
                service_list = services
                extraction.service_interest.services = service_list
                extraction.commercial.selected_services = service_list
                EXTRACTION_FIELDS.labels(category="service_interest").inc(len(service_list))
            if has_nda_request(
                message.normalized,
                negation_spans=message.negation_spans,
                counterfactual_spans=message.counterfactual_spans,
            ):
                extraction.document_request.requested_type = "nda"
            if has_agreement_request(
                message.normalized,
                negation_spans=message.negation_spans,
                counterfactual_spans=message.counterfactual_spans,
            ):
                extraction.document_request.requested_type = "agreement"
            if "?" in message.normalized:
                extraction.user_questions = [message.normalized]

            # Step 1: deterministic consultation fallback extraction.
            # Runs regardless of intent classification to catch missed consultations.
            known_tz = getattr(state, "preferred_timezone", None) if state is not None else None
            consultation_fields = extract_consultation(message.raw, known_timezone=known_tz)
            if consultation_fields:
                cr = extraction.consultation_request
                cr.requested = True

                def _s(key: str) -> str | None:
                    val = consultation_fields.get(key)
                    return str(val) if val else None

                cr.requested_date_text = _s("requested_date_text")
                cr.requested_time_text = _s("requested_time_text")
                cr.requested_datetime_text = _s("requested_datetime_text")
                cr.timezone_text = _s("timezone_text")
                cr.channel_preference = _s("channel_preference")
                cr.timezone_unknown = bool(consultation_fields.get("timezone_unknown"))
                EXTRACTION_FIELDS.labels(category="consultation").inc()

            return extraction


def _first_string(value: object) -> str | None:
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _first_int(value: object) -> int | None:
    if isinstance(value, list) and value and isinstance(value[0], int):
        return value[0]
    return None


# Matches one or more standalone integers (with optional commas), nothing else.
# Anchored so "85,000 words" does NOT match here (handled by the labelled path).
_BARE_INTEGERS_RE = re.compile(r"^[\s,]*(\d[\d,]*)(?:\s+(\d[\d,]*))?[\s,]*$")


def _extract_bare_length_numbers(text: str) -> list[int]:
    """Return integers from a message that is ONLY bare numbers.

    For example: "3000", "200 1000", "85,000" all return the integer list.
    Returns empty list when the message has other non-numeric content.
    """
    stripped = text.strip()
    m = _BARE_INTEGERS_RE.match(stripped)
    if not m:
        return []
    result = []
    for grp in m.groups():
        if grp is not None:
            try:
                result.append(int(grp.replace(",", "")))
            except ValueError:
                pass
    return [n for n in result if n > 0]


def _state_asked_for_length(state: "ThreadState") -> bool:
    """Return True when the preceding bot turn asked for word/page count."""
    qt = getattr(state, "current_question_type", None)
    if qt and any(kw in str(qt).lower() for kw in ("word", "page", "count", "length", "pricing")):
        return True
    # Also check the last next_question via context (slot tracker stores it in state).
    nq = getattr(state, "current_question_type", None)
    return nq in {"word_or_page_count", "word_count", "page_count", "length"}
