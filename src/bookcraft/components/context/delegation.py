from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Detection patterns — reusable groups, not one-off per-case checks
# ---------------------------------------------------------------------------

_DELEGATED_RE = re.compile(
    r"\b(?:you\s+decide|you\s+suggest|your\s+choice|use\s+your\s+own\s+creativity"
    r"|whatever\s+you\s+think|whatever\s+is\s+best|i\s+trust\s+your\s+team"
    r"|bookcraft\s+can\s+decide|you\s+guys\s+decide|come\s+up\s+with\s+your\s+own"
    r"|up\s+to\s+you)\b",
    re.IGNORECASE,
)

_UNKNOWN_RE = re.compile(
    r"\b(?:i\s+don'?t\s+know|i\s+do\s+not\s+know|not\s+sure|unsure|no\s+idea"
    r"|no\s+clue|i\s+can'?t\s+tell|i\s+don'?t\s+have\s+that|i\s+don'?t\s+remember"
    r"|haven'?t\s+decided)\b",
    re.IGNORECASE,
)

_NOT_APPLICABLE_RE = re.compile(
    r"\b(?:not\s+applicable|doesn'?t\s+apply|no\s+deadline|no\s+fixed\s+deadline"
    r"|no\s+word\s+count\s+yet|not\s+needed)\b",
    re.IGNORECASE,
)

_DECLINED_RE = re.compile(
    r"\b(?:skip\s+that|don'?t\s+ask\s+that|do\s+not\s+ask\s+that"
    r"|i\s+don'?t\s+want\s+to\s+answer|i\s+prefer\s+not\s+to\s+say"
    r"|just\s+show\s+me|just\s+continue|move\s+ahead\s+without\s+it)\b",
    re.IGNORECASE,
)

# Slot-inference keyword groups (longer/more-specific phrases first)
_SLOT_KEYWORDS: list[tuple[list[str], str]] = [
    (["cover style", "visual direction", "design idea", "cover illustration"], "cover_style"),
    (
        ["word count", "page count", "how many words", "how many pages", "word or page"],
        "word_or_page_count",
    ),
    (["manuscript stage", "draft status", "manuscript status"], "manuscript_stage"),
    (["deadline", "launch date", "launch window"], "deadline"),
    (["genre", "book category", "category"], "genre"),
]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SlotResolutionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: str
    status: Literal[
        "missing",
        "known",
        "unknown_by_user",
        "delegated",
        "declined",
        "not_applicable",
    ]
    source_turn_id: str | None = None
    reason: str | None = None
    forbidden_reask: bool = False
    confidence: float = 1.0
    # Project scoping — None means legacy/global (applies to any project).
    project_id: str | None = None


class DelegatedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected: bool
    status: Literal["not_delegated", "unknown_by_user", "delegated", "declined", "not_applicable"]
    target_slot: str | None = None
    confidence: float
    cue: str | None = None
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class DelegatedDecisionDetector:
    """Detects when a user declines, delegates, or marks a slot as unknown."""

    def detect(
        self,
        *,
        text: str,
        current_slot: str | None = None,
        response_plan_next_question: str | None = None,
        context_pack: Any | None = None,
    ) -> DelegatedDecision:
        audit: list[str] = []
        text_lower = text.casefold().strip()

        status, cue, confidence = _detect_signal(text_lower, audit)

        if status == "not_delegated":
            return DelegatedDecision(
                detected=False,
                status="not_delegated",
                confidence=0.0,
                audit=audit,
            )

        target_slot = _bind_to_slot(
            text_lower,
            current_slot=current_slot,
            response_plan_next_question=response_plan_next_question,
            context_pack=context_pack,
            audit=audit,
        )

        return DelegatedDecision(
            detected=True,
            status=status,
            target_slot=target_slot,
            confidence=confidence,
            cue=cue,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SignalStatus = Literal[
    "not_delegated", "unknown_by_user", "delegated", "declined", "not_applicable"
]


def _detect_signal(text: str, audit: list[str]) -> tuple[_SignalStatus, str | None, float]:
    m = _DELEGATED_RE.search(text)
    if m:
        cue = m.group(0)[:50]
        audit.append(f"signal:delegated:{cue}")
        return "delegated", cue, 0.92

    m = _UNKNOWN_RE.search(text)
    if m:
        cue = m.group(0)[:50]
        audit.append(f"signal:unknown_by_user:{cue}")
        return "unknown_by_user", cue, 0.88

    m = _NOT_APPLICABLE_RE.search(text)
    if m:
        cue = m.group(0)[:50]
        audit.append(f"signal:not_applicable:{cue}")
        return "not_applicable", cue, 0.90

    m = _DECLINED_RE.search(text)
    if m:
        cue = m.group(0)[:50]
        audit.append(f"signal:declined:{cue}")
        return "declined", cue, 0.85

    audit.append("signal:none")
    return "not_delegated", None, 0.0


def _bind_to_slot(
    text: str,
    *,
    current_slot: str | None,
    response_plan_next_question: str | None,
    context_pack: Any | None,
    audit: list[str],
) -> str | None:
    if current_slot:
        audit.append(f"bind:current_slot:{current_slot}")
        return current_slot

    if response_plan_next_question:
        audit.append(f"bind:response_plan:{response_plan_next_question}")
        return response_plan_next_question

    if context_pack is not None:
        allowed = getattr(context_pack, "allowed_next_questions", None) or []
        if allowed:
            audit.append(f"bind:allowed_next[0]:{allowed[0]}")
            return str(allowed[0])
        missing = getattr(context_pack, "missing_facts", None) or []
        if missing:
            audit.append(f"bind:missing[0]:{missing[0]}")
            return str(missing[0])

    for keywords, slot in _SLOT_KEYWORDS:
        for kw in keywords:
            if kw in text:
                audit.append(f"bind:text_infer:{slot}")
                return slot

    audit.append("bind:none")
    return None


def load_slot_statuses(raw_list: list[Any]) -> list[SlotResolutionStatus]:
    """Parse raw state dicts/objects into SlotResolutionStatus objects."""
    result: list[SlotResolutionStatus] = []
    for item in raw_list:
        if isinstance(item, dict):
            try:
                result.append(SlotResolutionStatus.model_validate(item))
            except Exception:  # noqa: BLE001,S110
                pass
        elif isinstance(item, SlotResolutionStatus):
            result.append(item)
    return result
