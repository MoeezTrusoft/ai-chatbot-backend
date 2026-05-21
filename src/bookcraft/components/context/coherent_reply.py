"""CoherentReplyResolver — maps short/indirect user replies to the pending slot context."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.domain.state import ThreadState

# ---------------------------------------------------------------------------
# Patterns for slot-value extraction
# ---------------------------------------------------------------------------

_WORD_COUNT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(\d{2,6})\s*,\s*000\b"),  # 60,000
    re.compile(r"\b(\d{2,6})\s+words?\b", re.IGNORECASE),  # 60000 words
    re.compile(r"\baround\s+(\d{2,6})\s*k\b", re.IGNORECASE),  # around 60k
    re.compile(r"\babout\s+(\d{2,6})\s*k\b", re.IGNORECASE),  # about 60k
    re.compile(r"\b(\d{2,6})\s*k\b", re.IGNORECASE),  # 60k
    re.compile(r"\baround\s+(\d{3,6})\b", re.IGNORECASE),  # around 60000
    re.compile(r"\babout\s+(\d{3,6})\b", re.IGNORECASE),  # about 60000
    re.compile(r"^\s*(\d{3,6})\s*$"),  # bare number 60000
]

_PAGE_COUNT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(\d+)\s+pages?\b", re.IGNORECASE),
    re.compile(r"\babout\s+(\d+)\s+pages?\b", re.IGNORECASE),
    re.compile(r"\baround\s+(\d+)\s+pages?\b", re.IGNORECASE),
    re.compile(r"\bapprox(?:imately)?\s+(\d+)\s+pages?\b", re.IGNORECASE),
]

_MANUSCRIPT_STATUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bjust\s+(?:rough\s+)?notes?\b|\brough\s+notes?\b", re.IGNORECASE),
        "rough_notes",
    ),
    (re.compile(r"\bvoice\s+memos?\b", re.IGNORECASE), "voice_memo"),
    (re.compile(r"\bjournal\s+entries?\b", re.IGNORECASE), "journal_entries"),
    (re.compile(r"\boutline\b", re.IGNORECASE), "outline"),
    (
        re.compile(
            r"\bpartial\s+draft\b|\bsome\s+chapters?\b|\bpartly\s+(?:written|done)\b|"
            r"\bhalf\s+(?:written|done|finished)\b",
            re.IGNORECASE,
        ),
        "partial_draft",
    ),
    (
        re.compile(
            r"\bcompleted?\s+(?:manuscript|draft)\b|\bfinished\b|\ball\s+done\b|"
            r"\bfully\s+written\b",
            re.IGNORECASE,
        ),
        "completed",
    ),
    (
        re.compile(r"\bstill\s+writing\b|\bin\s+progress\b|\bworking\s+on\s+it\b", re.IGNORECASE),
        "in_progress",
    ),
    (
        re.compile(
            r"\bjust\s+(?:an?\s+)?idea\b|\bonly\s+(?:an?\s+)?idea\b|\bidea\s+(?:stage|only|phase)\b",
            re.IGNORECASE,
        ),
        "idea",
    ),
    (re.compile(r"\bdraft\b", re.IGNORECASE), "draft"),
]

_CALL_TIME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"(?:\s+(?:morning|afternoon|evening|at\s+\d+(?:am|pm)?))?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\btomorrow(?:\s+(?:morning|afternoon|evening))?\b", re.IGNORECASE),
    re.compile(r"\bafter\s+\d+(?:\s*(?:am|pm))?\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(?:am|pm)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:this|next)\s+(?:week|monday|tuesday|wednesday|thursday|friday)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bany\s+(?:day|time|morning|afternoon|evening)\b", re.IGNORECASE),
    re.compile(r"\b(?:morning|afternoon|evening)\b", re.IGNORECASE),
]

# Pending-slot detection from the last assistant question text.
_WORD_COUNT_Q_RE = re.compile(
    r"\b(?:word\s+count|page\s+count|how\s+many\s+(?:words|pages)|"
    r"word\s+or\s+page|roughly\s+how\s+(?:long|many)|how\s+long)\b",
    re.IGNORECASE,
)
_MANUSCRIPT_Q_RE = re.compile(
    r"\b(?:manuscript\s+stage|what\s+stage|starting\s+from\s+scratch|"
    r"have\s+(?:a\s+)?draft|written\s+anything|how\s+far\s+along|"
    r"where\s+are\s+you\s+(?:with|on))\b",
    re.IGNORECASE,
)
_GENRE_Q_RE = re.compile(
    r"\b(?:what\s+(?:genre|type\s+of\s+book|kind\s+of)|which\s+genre|"
    r"fiction\s+or|memoir\s+or|what\s+kind\s+of\s+(?:book|story))\b",
    re.IGNORECASE,
)
_CONTACT_Q_RE = re.compile(
    r"\b(?:name\s+and\s+(?:email|phone)|email\s+or\s+phone|contact\s+(?:details?|info|information)|"
    r"best\s+(?:name|email|number)|your\s+name|reach\s+you|get\s+in\s+touch)\b",
    re.IGNORECASE,
)
_CALL_TIME_Q_RE = re.compile(
    r"\b(?:when\s+(?:are\s+you|would\s+you)|preferred\s+(?:time|day|date|slot)|"
    r"call\s+time|available\s+for\s+a\s+call|schedule\s+(?:a\s+)?(?:call|time)|"
    r"what\s+time\s+works)\b",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", flags=re.IGNORECASE)
_PHONE_RE = re.compile(r"\+?\d[\d\s().+-]{6,}\d")


class CoherentReplyResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved: bool = False
    slot_path: str | None = None
    value: Any | None = None
    confidence: float = 0.0
    source: str = "coherent_reply"
    audit: list[str] = Field(default_factory=list)


class CoherentReplyResolver:
    """
    Resolve short/partial/indirect user replies to the most likely pending slot.

    Priority:
    1. Explicit correction in current message.
    2. Answer to last assistant question / response_plan.next_question.
    3. Standalone extraction from current message.
    4. Existing confirmed state.
    5. Uncertain/candidate state.
    """

    def resolve(
        self,
        *,
        text: str,
        state: ThreadState,
        context_pack: Any | None = None,
        last_assistant_question: str | None = None,
        next_question: str | None = None,
    ) -> list[CoherentReplyResolution]:
        """
        Resolve user text to a list of slot resolutions.

        Returns an empty list when nothing can be resolved.
        Resolutions carry a slot_path and value for StateApplier-compatible use.
        """
        del state, context_pack  # available for future confidence signals

        pending = _infer_pending_slot(last_assistant_question, next_question)

        resolutions: list[CoherentReplyResolution] = []

        if pending == "word_or_page_count":
            resolutions.extend(_resolve_word_page_count(text))
        elif pending == "manuscript_stage":
            resolutions.extend(_resolve_manuscript_status(text))
        elif pending == "genre":
            resolutions.extend(_resolve_genre_uncertain(text))
        elif pending in {"name_and_email_or_phone", "contact"}:
            resolutions.extend(_resolve_contact(text))
        elif pending in {"call_time", "consultation_interest"}:
            resolutions.extend(_resolve_call_time(text))
        else:
            # No clear pending slot: try all value resolvers (lower confidence).
            resolutions.extend(_resolve_word_page_count(text))
            resolutions.extend(_resolve_manuscript_status(text))
            resolutions.extend(_resolve_contact(text))

        return resolutions


# ---------------------------------------------------------------------------
# Pending-slot inference
# ---------------------------------------------------------------------------


def _infer_pending_slot(
    last_assistant_question: str | None,
    next_question: str | None,
) -> str | None:
    question = last_assistant_question or ""

    if _WORD_COUNT_Q_RE.search(question):
        return "word_or_page_count"
    if _MANUSCRIPT_Q_RE.search(question):
        return "manuscript_stage"
    if _GENRE_Q_RE.search(question):
        return "genre"
    if _CONTACT_Q_RE.search(question):
        return "name_and_email_or_phone"
    if _CALL_TIME_Q_RE.search(question):
        return "call_time"

    # Fall back to the next_question key.
    nq = next_question or ""
    if nq in {"word_or_page_count", "genre", "manuscript_stage", "deadline", "cover_style"}:
        return nq
    if nq in {"name_and_email_or_phone"}:
        return "name_and_email_or_phone"
    if nq in {"consultation_interest"}:
        return "call_time"

    return None


# ---------------------------------------------------------------------------
# Value resolvers
# ---------------------------------------------------------------------------


def _resolve_word_page_count(text: str) -> list[CoherentReplyResolution]:
    for pattern in _PAGE_COUNT_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                value = int(m.group(1).replace(",", ""))
                return [
                    CoherentReplyResolution(
                        resolved=True,
                        slot_path="project.page_count",
                        value=value,
                        confidence=0.8,
                        audit=[f"page_count:{value}"],
                    )
                ]
            except (ValueError, IndexError):
                pass

    for pattern in _WORD_COUNT_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                raw = m.group(1).replace(",", "")
                value = int(raw)
                if "k" in m.group(0).lower() and value < 2000:
                    value *= 1000
                return [
                    CoherentReplyResolution(
                        resolved=True,
                        slot_path="project.word_count",
                        value=value,
                        confidence=0.8,
                        audit=[f"word_count:{value}"],
                    )
                ]
            except (ValueError, IndexError):
                pass

    return []


def _resolve_manuscript_status(text: str) -> list[CoherentReplyResolution]:
    for pattern, status in _MANUSCRIPT_STATUS_PATTERNS:
        if pattern.search(text):
            return [
                CoherentReplyResolution(
                    resolved=True,
                    slot_path="project.manuscript_status",
                    value=status,
                    confidence=0.75,
                    audit=[f"manuscript_status:{status}"],
                )
            ]
    return []


def _resolve_genre_uncertain(text: str) -> list[CoherentReplyResolution]:
    from bookcraft.components.preprocessor.detectors.genre_uncertainty import (
        detect_genre_uncertainty,
    )

    result = detect_genre_uncertainty(text)
    if result.uncertain:
        return [
            CoherentReplyResolution(
                resolved=True,
                slot_path="project.genre",
                value=None,  # uncertain — do NOT confirm genre
                confidence=0.0,
                audit=["genre_uncertain:not_confirmed"] + result.audit,
            )
        ]
    return []


def _resolve_contact(text: str) -> list[CoherentReplyResolution]:
    resolutions: list[CoherentReplyResolution] = []

    email_m = _EMAIL_RE.search(text)
    if email_m:
        resolutions.append(
            CoherentReplyResolution(
                resolved=True,
                slot_path="personal.email",
                value=email_m.group(0),
                confidence=0.9,
                audit=["contact_email"],
            )
        )

    phone_m = _PHONE_RE.search(text)
    if phone_m:
        resolutions.append(
            CoherentReplyResolution(
                resolved=True,
                slot_path="personal.phone",
                value=phone_m.group(0),
                confidence=0.85,
                audit=["contact_phone"],
            )
        )

    return resolutions


def _resolve_call_time(text: str) -> list[CoherentReplyResolution]:
    for pattern in _CALL_TIME_PATTERNS:
        m = pattern.search(text)
        if m:
            return [
                CoherentReplyResolution(
                    resolved=True,
                    slot_path="preferred_call_time",
                    value=m.group(0),
                    confidence=0.7,
                    audit=[f"call_time:{m.group(0)}"],
                )
            ]
    return []
