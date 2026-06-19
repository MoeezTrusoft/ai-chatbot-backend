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
    REQUESTED_PHONE_NEEDED = "requested_phone_needed"
    REQUESTED_TIME_NEEDED = "requested_time_needed"
    # Customer gave an *indefinite* time (e.g. "anytime", "next week", "Friday");
    # we offer concrete half-hour slots to pin it to a definite one.
    REQUESTED_TIME_SLOTS_OFFERED = "requested_time_slots_offered"
    TIME_CAPTURED_NEEDS_TIMEZONE = "time_captured_needs_timezone"
    READY_TO_SCHEDULE = "ready_to_schedule"
    PENDING_CONFIRMATION = "pending_confirmation"
    SCHEDULED = "scheduled"
    HANDOFF_CREATED = "handoff_created"


# Stages that mean a consultation request is genuinely in flight and should persist
# across turns (as opposed to NONE, or the ConsultationObjectiveEngine's own vocabulary
# like "engaging" which does NOT indicate a request).
_ACTIVE_REQUEST_STAGES = frozenset(
    {
        ConsultationStage.REQUESTED_CONTACT_NEEDED,
        ConsultationStage.REQUESTED_PHONE_NEEDED,
        ConsultationStage.REQUESTED_TIME_NEEDED,
        ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED,
        ConsultationStage.TIME_CAPTURED_NEEDS_TIMEZONE,
        ConsultationStage.READY_TO_SCHEDULE,
        ConsultationStage.PENDING_CONFIRMATION,
        ConsultationStage.SCHEDULED,
        ConsultationStage.HANDOFF_CREATED,
    }
)


def _prior_stage_is_active_request(prior_stage: Any) -> bool:
    """True only when the prior-turn stage is one of THIS reducer's request stages.

    `prior_stage` is the consultation stage captured at the start of the turn, before any
    engine overwrote it. Values foreign to ConsultationStage (e.g. the objective engine's
    "engaging") normalise to "no active request".
    """
    if not prior_stage:
        return False
    try:
        return ConsultationStage(str(prior_stage)) in _ACTIVE_REQUEST_STAGES
    except ValueError:
        return False


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


# A call time is bookable-*definite* only when it pins down BOTH a specific day and
# a specific clock time — e.g. "Tuesday at 3pm", "June 24 at 10:30am". Anything
# vaguer ("anytime", "next week", "Friday", "afternoon", "3pm" with no day) is
# indefinite: we should offer concrete slots rather than silently coerce it.
_CLOCK_TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.IGNORECASE)
_SPECIFIC_DAY_RE = re.compile(
    r"\b(?:"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|today|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}"
    r")\b",
    re.IGNORECASE,
)
# "next Friday" / "this Monday" still name a specific weekday — fine. But "next week"
# / "next weekend" name no day, so exclude those bare phrases from the day check.
_VAGUE_DAY_RE = re.compile(r"\bnext\s+(?:week|weekend)\b", re.IGNORECASE)


def is_definite_call_time(text: str | None) -> bool:
    """True when the call-time text names BOTH a specific day and a clock time."""
    if not text:
        return False
    has_clock = bool(_CLOCK_TIME_RE.search(text))
    if not has_clock:
        return False
    # A bare "next week"/"next weekend" with no weekday is not a specific day.
    day_text = _VAGUE_DAY_RE.sub(" ", text)
    has_day = bool(_SPECIFIC_DAY_RE.search(day_text))
    return has_day


def reduce_consultation_state(
    *,
    state: Any,  # ThreadState
    message: str,
    intent: Any,  # IntentVote
    contact_ready: bool,
    action_plan: Any | None = None,
    action_result: Any | None = None,
    has_email: bool = False,
    has_phone: bool = False,
    require_phone: bool = False,
    prior_stage: Any | None = None,
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
    # Persisted-request signal. Do NOT read the live `state.consultation_stage`: by the
    # time this reducer runs, the ConsultationObjectiveEngine has already overwritten that
    # field with its OWN vocabulary (e.g. "engaging" for any ordinary chat turn). Treating
    # a non-empty value as a request made EVERY turn look like a consultation request and
    # latched the bot into REQUESTED_CONTACT_NEEDED from turn 1. Use `prior_stage` (captured
    # before the objective engine ran) and count only genuine request stages from this
    # reducer's own enum.
    consultation_from_state = _prior_stage_is_active_request(prior_stage) or bool(
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

    # A consultation HARD-requires a phone number (unlike a lead, which may be
    # email-only). Block scheduling — keep asking for the phone — until one is present.
    # This is a hard gate: can_schedule stays False and we never reach READY_TO_SCHEDULE
    # without a phone, so no booking is created without it.
    if require_phone and not has_phone:
        audit.append("signal:phone_required_for_consultation")
        return ConsultationStateDecision(
            stage=ConsultationStage.REQUESTED_PHONE_NEEDED,
            contact_ready=True,
            consultation_requested=True,
            preferred_call_time=preferred_call_time,
            next_question="missing_phone",
            can_schedule=False,
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

    # Contact + a time window that is INDEFINITE ("anytime", "next week", "Friday",
    # "afternoon") — don't silently coerce it into a booking. Offer concrete
    # half-hour slots so the customer narrows it to a definite day+time. Loop-safe:
    # once they pick a slot the time becomes definite and we fall through to booking.
    if not is_definite_call_time(preferred_call_time):
        audit.append(f"signal:indefinite_time_offer_slots(time={preferred_call_time!r})")
        return ConsultationStateDecision(
            stage=ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED,
            contact_ready=True,
            consultation_requested=True,
            preferred_call_time=preferred_call_time,
            can_schedule=False,
            next_question="preferred_call_time_slots",
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
    # Also honour a timezone captured into personal.timezone (this is where the LLM
    # extractor stores "Eastern time" → "America/New_York"). Without this, a booking
    # whose timezone only lives in personal.timezone stalls forever at
    # time_captured_needs_timezone even though the timezone IS known (BUG-6040).
    _tz_from_personal = getattr(getattr(getattr(state, "personal", None), "timezone", None), "value", None)
    timezone_unknown = is_relative_window and not (
        getattr(state, "preferred_timezone", None) or _tz_from_consultation or _tz_from_personal
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
