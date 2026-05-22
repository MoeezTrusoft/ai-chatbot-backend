"""Canonical consultation state reducer.

Single source of truth for what stage the consultation is at and what
the bot should ask next. Reads from all relevant state fields and
produces a deterministic ConsultationStateDecision.

Engines compute. Claude writes.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConsultationStage(StrEnum):
    NONE = "none"
    REQUESTED_CONTACT_NEEDED = "requested_contact_needed"
    REQUESTED_TIME_NEEDED = "requested_time_needed"
    TIME_CAPTURED_NEEDS_TIMEZONE = "time_captured_needs_timezone"
    READY_TO_SCHEDULE = "ready_to_schedule"
    PENDING_CONFIRMATION = "pending_confirmation"
    SCHEDULED = "scheduled"
    HANDOFF_CREATED = "handoff_created"


class ConsultationStateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ConsultationStage = ConsultationStage.NONE
    contact_ready: bool = False
    consultation_requested: bool = False
    preferred_call_time: str | None = None
    timezone_needed: bool = False
    can_schedule: bool = False
    next_question: str | None = None
    stop_discovery: bool = False
    is_status_question: bool = False
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Consultation request detection
# ---------------------------------------------------------------------------

_CONSULTATION_REQUEST_RE = re.compile(
    r"\b(?:free\s+consultation|schedule\s+(?:it|a\s+call|the\s+consultation)|"
    r"book\s+(?:it|a\s+call|the\s+consultation|me\s+in)|"
    r"book\s+a\s+consultation|"
    r"talk\s+to\s+(?:someone|a\s+specialist|your\s+team)|"
    r"speak\s+to\s+(?:a\s+consultant|someone)|"
    r"call\s+me|connect\s+me\s+with|"
    r"consultation\s+(?:you\s+advertised|on\s+your|mentioned)|"
    r"just\s+schedule\s+it|schedule\s+(?:the\s+)?consultation|"
    r"free\s+call|free\s+review|"
    r"set\s+up\s+a\s+(?:call|consultation|meeting))\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Consultation status question detection
# ---------------------------------------------------------------------------

_STATUS_QUESTION_RE = re.compile(
    r"\b(?:have\s+(?:my|the)\s+consultation\s+been\s+scheduled|"
    r"is\s+(?:my|the)\s+consultation\s+(?:scheduled|confirmed|booked)|"
    r"(?:what|which)\s+time\s+(?:is|for)\s+(?:my|the)\s+consultation|"
    r"when\s+is\s+(?:my|the)\s+(?:call|consultation|appointment)|"
    r"did\s+you\s+book\s+it|is\s+it\s+booked|"
    r"has\s+(?:the\s+)?(?:consultation|appointment|call)\s+been\s+(?:scheduled|booked|confirmed)|"
    r"appointment\s+(?:time|confirmed|scheduled)|"
    r"consultation\s+(?:time|confirmed|status|update))\b",
    re.IGNORECASE,
)

# Time-window patterns for extraction
_CALL_TIME_FULL_RE = re.compile(
    r"\b(?:(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:\s+(?:morning|afternoon|evening|at\s+\d+\s*(?:am|pm)?|around\s+\d+))?|"
    r"tomorrow(?:\s+(?:morning|afternoon|evening|at\s+\d+\s*(?:am|pm)?|around\s+\d+))?|"
    r"\d+\s*(?:am|pm)|"
    r"(?:morning|afternoon|evening|anytime))\b",
    re.IGNORECASE,
)


def _extract_call_time(text: str) -> str | None:
    m = _CALL_TIME_FULL_RE.search(text)
    return m.group(0).strip() if m else None


def reduce_consultation_state(
    *,
    state: Any,  # ThreadState
    message: str,
    intent: Any,  # IntentVote
    contact_ready: bool,
    action_plan: Any | None = None,
    action_result: Any | None = None,
) -> ConsultationStateDecision:
    """Reduce all consultation signals into a single deterministic decision.

    This is the canonical source of truth for what the bot should do next
    regarding the consultation. Called AFTER contact sync but BEFORE response planning.
    """
    audit: list[str] = []
    from bookcraft.domain.enums import QueryIntentType

    # ── Detect status question ────────────────────────────────────────────
    is_status_question = bool(_STATUS_QUESTION_RE.search(message))
    if is_status_question:
        audit.append("signal:status_question")

    # ── Detect consultation request ───────────────────────────────────────
    query_primary = getattr(intent, "query_primary", None)
    consultation_from_intent = query_primary == QueryIntentType.CONSULTATION_REQUEST
    consultation_from_text = bool(_CONSULTATION_REQUEST_RE.search(message))
    consultation_from_state = bool(getattr(state, "consultation_stage", None)) or bool(
        getattr(state, "sales_actions", None) and state.sales_actions.consultation.requested
    )
    consultation_requested = (
        consultation_from_intent or consultation_from_text or consultation_from_state
    )
    if consultation_requested:
        audit.append(
            f"signal:consultation_requested(intent={consultation_from_intent},"
            f"text={consultation_from_text},state={consultation_from_state})"
        )

    # ── Extract preferred call time ───────────────────────────────────────
    # Prefer the most specific time phrase available.
    time_from_message = _extract_call_time(message)
    time_from_state = getattr(state, "preferred_call_time", None)
    time_from_nested = (
        state.sales_actions.consultation.preferred_time_window
        if hasattr(state, "sales_actions") and state.sales_actions.consultation
        else None
    )
    preferred_call_time = time_from_message or time_from_state or time_from_nested
    if preferred_call_time:
        audit.append(f"signal:preferred_call_time={preferred_call_time!r}")

    # ── Check for confirmed appointment ──────────────────────────────────
    confirmed_appointment_id = (
        state.sales_actions.consultation.confirmed_appointment_id
        if hasattr(state, "sales_actions")
        else None
    )
    handoff_created = getattr(state, "consultation_handoff_created", False)

    # Check action result for successful scheduling.
    from bookcraft.components.actions.schemas import ActionType

    action_scheduled = (
        action_result is not None
        and getattr(action_result, "success", False)
        and getattr(action_result, "action_type", None) == ActionType.SCHEDULE_CONSULTATION
    )
    if action_scheduled:
        audit.append("signal:action_result_scheduled")
        return ConsultationStateDecision(
            stage=ConsultationStage.SCHEDULED,
            contact_ready=contact_ready,
            consultation_requested=True,
            preferred_call_time=preferred_call_time,
            can_schedule=False,  # Already scheduled.
            stop_discovery=True,
            is_status_question=is_status_question,
            audit=audit,
        )

    if confirmed_appointment_id or handoff_created:
        audit.append("signal:confirmed_appointment_exists")
        return ConsultationStateDecision(
            stage=ConsultationStage.SCHEDULED,
            contact_ready=contact_ready,
            consultation_requested=True,
            preferred_call_time=preferred_call_time,
            can_schedule=False,
            stop_discovery=True,
            is_status_question=is_status_question,
            audit=audit,
        )

    # Check pending confirmation.
    from bookcraft.components.actions.schemas import ActionStatus

    plan_status = getattr(action_plan, "status", None)
    plan_type = getattr(action_plan, "action_type", None)
    plan_is_pending = (
        plan_type == ActionType.SCHEDULE_CONSULTATION
        and plan_status == ActionStatus.NEEDS_CONFIRMATION
    )
    consultation_pending = (
        state.sales_actions.consultation.pending_confirmation
        if hasattr(state, "sales_actions")
        else False
    )
    if plan_is_pending or consultation_pending:
        audit.append("signal:pending_confirmation")
        return ConsultationStateDecision(
            stage=ConsultationStage.PENDING_CONFIRMATION,
            contact_ready=contact_ready,
            consultation_requested=True,
            preferred_call_time=preferred_call_time,
            can_schedule=False,
            stop_discovery=True,
            is_status_question=is_status_question,
            audit=audit,
        )

    # ── No confirmed appointment: determine what's missing ────────────────
    if not consultation_requested and not is_status_question:
        audit.append("signal:no_consultation_request")
        return ConsultationStateDecision(
            stage=ConsultationStage.NONE,
            contact_ready=contact_ready,
            consultation_requested=False,
            preferred_call_time=preferred_call_time,
            is_status_question=False,
            audit=audit,
        )

    if not contact_ready:
        audit.append("signal:contact_needed")
        return ConsultationStateDecision(
            stage=ConsultationStage.REQUESTED_CONTACT_NEEDED,
            contact_ready=False,
            consultation_requested=True,
            preferred_call_time=preferred_call_time,
            next_question="name_and_email_or_phone",
            stop_discovery=True,
            is_status_question=is_status_question,
            audit=audit,
        )

    # Contact ready. Do we have a time window?
    if not preferred_call_time:
        audit.append("signal:time_window_needed")
        return ConsultationStateDecision(
            stage=ConsultationStage.REQUESTED_TIME_NEEDED,
            contact_ready=True,
            consultation_requested=True,
            next_question="preferred_call_time",
            stop_discovery=True,
            is_status_question=is_status_question,
            audit=audit,
        )

    # Contact + time window — check if timezone is needed.
    # "Friday afternoon" is a relative window; we need exact time/timezone to lock in.
    is_relative_window = not any(c.isdigit() for c in (preferred_call_time or "")) or bool(
        re.search(r"\b(?:morning|afternoon|evening|anytime)\b", preferred_call_time or "", re.I)
    )
    _tz_from_consultation = (
        state.sales_actions.consultation.customer_timezone
        if hasattr(state, "sales_actions")
        else None
    )
    timezone_unknown = is_relative_window and not (
        getattr(state, "preferred_timezone", None) or _tz_from_consultation
    )

    if timezone_unknown:
        audit.append(f"signal:timezone_needed(relative_window={is_relative_window})")
        return ConsultationStateDecision(
            stage=ConsultationStage.TIME_CAPTURED_NEEDS_TIMEZONE,
            contact_ready=True,
            consultation_requested=True,
            preferred_call_time=preferred_call_time,
            timezone_needed=True,
            can_schedule=False,
            next_question="preferred_call_timezone",
            stop_discovery=True,
            is_status_question=is_status_question,
            audit=audit,
        )

    # All details present — ready to schedule.
    audit.append("signal:ready_to_schedule")
    return ConsultationStateDecision(
        stage=ConsultationStage.READY_TO_SCHEDULE,
        contact_ready=True,
        consultation_requested=True,
        preferred_call_time=preferred_call_time,
        timezone_needed=False,
        can_schedule=True,
        stop_discovery=True,
        is_status_question=is_status_question,
        audit=audit,
    )


def user_asks_consultation_status(text: str) -> bool:
    """Return True when the user is asking about their consultation status."""
    return bool(_STATUS_QUESTION_RE.search(text))
