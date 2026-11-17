"""The call-now conversation: wording, and what each visitor answer leads to.

Rule set M-07 to M-09, H-02 to H-04, K-03. Everything here is deterministic.
The lines the visitor sees are fixed templates so that "right now" can never be
said outside callback hours and "within 5 minutes" is only said once a callback
request has actually been stored for the team.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.callbacks.hours import CallbackHours, safe_zone
from bookcraft.components.callbacks.schemas import CallbackFlowState, CallbackMode
from bookcraft.components.sales.call_time import parse_call_time

Outcome = Literal[
    "request_now",        # free now and a number is known: store the request
    "request_scheduled",  # a time inside callback hours and a number are known
    "ask_phone_now",
    "ask_phone_scheduled",
    "ask_time",           # not free now: ask once for a time that suits
    "ask_time_in_window",  # the time given falls outside callback hours
    "ask_weekday",        # the day given is a weekend
    "hours_closed",       # said yes, but call-now is no longer available
    "prefers_chat",
    "unhandled",          # the message was not an answer to the pending step
]

_AFFIRM_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|ya|sure|ok(?:ay)?|absolutely|definitely|of\s+course|please|"
    r"go\s+ahead|sounds\s+good|that\s+works|works\s+for\s+me|i\s+am|i'?m\s+free|free\s+now|"
    r"call\s+(?:me\s+)?now|call\s+me|now\s+is\s+(?:good|fine|great)|right\s+now|now)\b",
    re.IGNORECASE,
)
_NOT_NOW_RE = re.compile(
    r"\b(?:not\s+(?:right\s+)?now|not\s+at\s+the\s+moment|not\s+yet|later|another\s+time|"
    r"some\s+other\s+time|busy|at\s+work|in\s+a\s+meeting|driving|can'?t\s+(?:talk|take\s+a\s+call)"
    r"(?:\s+(?:right\s+)?now)?|tomorrow|tonight|this\s+(?:afternoon|evening)|after\s+\d)\b"
    r"|^\s*(?:no|nope|nah)\b[\s.!,]*(?:thanks|thank\s+you)?[\s.!]*$",
    re.IGNORECASE,
)
_PREFERS_CHAT_RE = re.compile(
    r"\b(?:prefer|rather|keep|stay|stick|continue)\b.{0,24}\b(?:chat|chatting|text(?:ing)?|"
    r"typing|messag(?:e|ing)|email|here|writing)\b"
    r"|\b(?:no|don'?t|do\s+not|not)\b.{0,16}\b(?:call|calls|phone|phoning|ring)\b"
    r"|\b(?:just\s+)?(?:email|e-mail|text|message)\s+me\b"
    r"|\bcan\s+(?:we|you)\s+(?:just\s+)?(?:chat|text|email|message)\b"
    r"|\b(?:bad|not\s+good|uncomfortable)\s+(?:at|with|on)\s+(?:calls?|calling|the\s+phone)\b",
    re.IGNORECASE,
)
_WEEKEND_RE = re.compile(r"\b(?:saturday|sunday|sat|sun|weekend)\b", re.IGNORECASE)
_EVENING_RE = re.compile(r"\b(?:evening|night|tonight|late)\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?(am|pm)$", re.IGNORECASE)

# Every line is within the mobile limit of 200 characters (M-02) and carries no dash.
_WORDING: dict[str, list[str]] = {
    "offer_now_number_known": [
        "Are you free for a quick call right now? We can call you on the number ending in "
        "{last4} and explain everything.",
        "Would a quick call right now help? We can ring the number ending in {last4} and "
        "walk you through it.",
    ],
    "offer_now_no_number": [
        "Are you free for a quick call right now? Send me the best number and we'll call you "
        "straight away to explain everything.",
        "Would a quick call right now help? Share the best number and our team will ring you "
        "straight away.",
    ],
    "offer_schedule": [
        "Our team is available from {window}. What time suits you for a call?",
        "We can call you {window}. What time works best for you?",
    ],
    "ask_time": [
        "No problem. Our team calls {window}. What time suits you?",
    ],
    "ask_time_in_window": [
        "Our team calls {window}. What time in that window suits you?",
    ],
    "ask_weekday": [
        "Our team calls Monday to Friday, {hours_only}. Which weekday and time suits you?",
    ],
    "hours_closed": [
        "Our team has just finished calls for today. We call {window}. What time suits you?",
    ],
    "ask_phone": [
        "What's the best number for our team to call you on?",
    ],
    "call_confirmed": [
        "Thank you. You'll get a call from our team within {minutes} minutes.",
    ],
    "call_time_agreed": [
        "Thank you. Our team will call you {time}, {timezone}.",
    ],
    "prefers_chat_email_known": [
        "No problem, we can keep going right here. What would you like to know about your book?",
    ],
    "prefers_chat_need_email": [
        "No problem, we can keep going right here. What's the best email for you, in case we "
        "get cut off?",
    ],
}

MOBILE_MAX_CHARS = 200


class CallbackTurnResult(BaseModel):
    """What the call flow decided for this visitor message."""

    model_config = ConfigDict(extra="forbid")

    outcome: Outcome = "unhandled"
    # The deterministic reply, when the flow answers this turn itself.
    text: str | None = None
    wording_key: str | None = None
    request_mode: CallbackMode | None = None
    requested_time_text: str | None = None
    customer_timezone: str | None = None
    expects_reply: bool = True
    reply_kind: str | None = None
    audit: list[str] = Field(default_factory=list)

    @property
    def handled(self) -> bool:
        return self.outcome != "unhandled"


def _clock_to_24h(clock: str | None) -> tuple[int, int] | None:
    match = _CLOCK_RE.match((clock or "").strip())
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == "pm":
        hour += 12
    return hour, int(match.group(2) or 0)


def _timezone_label(visitor_tz: str | None) -> str:
    """A readable label for the confirmation line ("Central Time" when unknown)."""
    if safe_zone(visitor_tz) is None:
        return "Central Time"
    city = visitor_tz.split("/")[-1].replace("_", " ")  # type: ignore[union-attr]
    return f"{city} time"


@dataclass(slots=True)
class CallbackFlow:
    hours: CallbackHours

    # ------------------------------------------------------------------ wording
    def _render(self, flow: CallbackFlowState, kind: str, **values: object) -> tuple[str, str]:
        """Pick the first wording of *kind* not yet used this visit (G-08)."""
        options = _WORDING[kind]
        index = next(
            (i for i in range(len(options)) if f"{kind}:{i}" not in flow.wordings_used),
            len(options) - 1,
        )
        key = f"{kind}:{index}"
        if key not in flow.wordings_used:
            flow.wordings_used.append(key)
        return options[index].format(**values), key

    def _window(self, visitor_tz: str | None, now_utc: datetime | None) -> str:
        phrase = self.hours.window_phrase()
        local = self.hours.local_window_phrase(visitor_tz, now_utc)
        return f"{phrase} ({local})" if local else phrase

    def _hours_only(self) -> str:
        return self.hours.window_phrase().split(",")[0]

    # ------------------------------------------------------------------- offers
    def offer_text(
        self,
        flow: CallbackFlowState,
        *,
        action: str,
        phone_last4: str | None,
        visitor_tz: str | None = None,
        reply_index: int = 0,
        now_utc: datetime | None = None,
    ) -> tuple[str, str]:
        """Build the offer line and record it as the pending step (M-07, M-08)."""
        if action == "offer_now":
            if phone_last4:
                text, key = self._render(flow, "offer_now_number_known", last4=phone_last4)
            else:
                text, key = self._render(flow, "offer_now_no_number")
            flow.pending = "offer_now"
        else:
            text, key = self._render(
                flow, "offer_schedule", window=self._window(visitor_tz, now_utc)
            )
            flow.pending = "ask_time"
        flow.pending_time_text = None
        flow.offers_this_visit += 1
        flow.last_offer_reply_index = reply_index
        return text, key

    # ------------------------------------------------------------------ answers
    def interpret(
        self,
        flow: CallbackFlowState,
        *,
        message: str,
        phone_known: bool,
        email_known: bool,
        phone_in_message: bool,
        visitor_tz: str | None = None,
        now_utc: datetime | None = None,
    ) -> CallbackTurnResult:
        """Read the visitor's answer to the pending call step (M-09).

        Mutates *flow* to the next step. Returns ``unhandled`` when the message is
        not an answer to what was asked, in which case the offer is left behind
        and the normal reply pipeline takes the turn.
        """
        step = flow.pending
        if step is None:
            return CallbackTurnResult()
        text = message or ""
        audit = [f"pending:{step}"]

        # A preference for chat or email ends the call flow for this visit.
        if _PREFERS_CHAT_RE.search(text) and not phone_in_message:
            flow.pending = None
            flow.pending_time_text = None
            flow.declined_this_visit = True
            kind = "prefers_chat_email_known" if email_known else "prefers_chat_need_email"
            reply, key = self._render(flow, kind)
            return CallbackTurnResult(
                outcome="prefers_chat", text=reply, wording_key=key, audit=[*audit, "prefers_chat"]
            )

        if step in ("offer_now", "need_phone_now"):
            not_now = step == "offer_now" and bool(_NOT_NOW_RE.search(text))
            if step == "offer_now":
                free_now = (bool(_AFFIRM_RE.search(text)) or phone_in_message) and not not_now
            else:
                free_now = phone_in_message
            if free_now:
                if not self.hours.call_now_available(now_utc):
                    # Callback hours ended while we were talking (scenario 39).
                    flow.pending = "ask_time"
                    reply, key = self._render(
                        flow, "hours_closed", window=self._window(visitor_tz, now_utc)
                    )
                    return CallbackTurnResult(
                        outcome="hours_closed", text=reply, wording_key=key,
                        audit=[*audit, "call_now_closed"],
                    )
                if not phone_known:
                    flow.pending = "need_phone_now"
                    reply, key = self._render(flow, "ask_phone")
                    return CallbackTurnResult(
                        outcome="ask_phone_now", text=reply, wording_key=key,
                        audit=[*audit, "need_phone"],
                    )
                flow.pending = None
                reply, key = self._render(
                    flow, "call_confirmed", minutes=self.hours.callback_minutes
                )
                return CallbackTurnResult(
                    outcome="request_now", text=reply, wording_key=key, request_mode="now",
                    expects_reply=False, reply_kind="call_confirmed",
                    audit=[*audit, "free_now"],
                )
            if not_now:
                parsed = self._scheduled(flow, text, phone_known, visitor_tz, now_utc, audit)
                if parsed is not None:
                    return parsed
                flow.pending = "ask_time"
                reply, key = self._render(
                    flow, "ask_time", window=self._window(visitor_tz, now_utc)
                )
                return CallbackTurnResult(
                    outcome="ask_time", text=reply, wording_key=key, audit=[*audit, "not_now"]
                )
            flow.pending = None
            return CallbackTurnResult(audit=[*audit, "not_an_answer"])

        if step == "ask_time":
            parsed = self._scheduled(flow, text, phone_known, visitor_tz, now_utc, audit)
            if parsed is not None:
                return parsed
            flow.pending = None  # asked once; do not loop on the time question
            return CallbackTurnResult(audit=[*audit, "no_time_found"])

        if step == "need_phone_scheduled":
            if phone_in_message or phone_known:
                time_text = flow.pending_time_text
                flow.pending = None
                flow.pending_time_text = None
                label = _timezone_label(visitor_tz)
                reply, key = self._render(
                    flow, "call_time_agreed", time=time_text or "at the time you chose",
                    timezone=label,
                )
                return CallbackTurnResult(
                    outcome="request_scheduled", text=reply, wording_key=key,
                    request_mode="scheduled", requested_time_text=time_text,
                    customer_timezone=visitor_tz if safe_zone(visitor_tz) else self.hours.tz_name,
                    expects_reply=False, reply_kind="call_scheduled",
                    audit=[*audit, "phone_received"],
                )
            flow.pending = None
            flow.pending_time_text = None
            return CallbackTurnResult(audit=[*audit, "no_phone_found"])

        flow.pending = None
        return CallbackTurnResult(audit=[*audit, "unknown_step"])

    def _scheduled(
        self,
        flow: CallbackFlowState,
        text: str,
        phone_known: bool,
        visitor_tz: str | None,
        now_utc: datetime | None,
        audit: list[str],
    ) -> CallbackTurnResult | None:
        """Handle a message that names a call time. None when it names no time at all."""
        parts = parse_call_time(text, allow_numeric=True)
        if parts.is_empty or parts.window == "anytime" and not parts.day:
            return None
        if _WEEKEND_RE.search(parts.day or ""):
            flow.pending = "ask_time"
            reply, key = self._render(flow, "ask_weekday", hours_only=self._hours_only())
            return CallbackTurnResult(
                outcome="ask_weekday", text=reply, wording_key=key, audit=[*audit, "weekend"]
            )
        clock = _clock_to_24h(parts.clock)
        outside = bool(_EVENING_RE.search(parts.window or ""))
        if clock is not None:
            hour, minute = self.hours.to_business_clock(clock[0], clock[1], visitor_tz, now_utc)
            outside = not self.hours.hour_in_window(hour, minute)
        if outside:
            # Scenario 38: a time outside callback hours is not accepted.
            flow.pending = "ask_time"
            reply, key = self._render(
                flow, "ask_time_in_window", window=self._window(visitor_tz, now_utc)
            )
            return CallbackTurnResult(
                outcome="ask_time_in_window", text=reply, wording_key=key,
                audit=[*audit, "outside_window"],
            )
        time_text = parts.to_text() or ""
        if parts.day is None and parts.clock and not parts.is_range:
            time_text = f"at {parts.clock}"
        if not phone_known:
            flow.pending = "need_phone_scheduled"
            flow.pending_time_text = time_text
            reply, key = self._render(flow, "ask_phone")
            return CallbackTurnResult(
                outcome="ask_phone_scheduled", text=reply, wording_key=key,
                requested_time_text=time_text, audit=[*audit, "time_ok_need_phone"],
            )
        flow.pending = None
        flow.pending_time_text = None
        reply, key = self._render(
            flow, "call_time_agreed", time=time_text, timezone=_timezone_label(visitor_tz)
        )
        return CallbackTurnResult(
            outcome="request_scheduled", text=reply, wording_key=key, request_mode="scheduled",
            requested_time_text=time_text,
            customer_timezone=visitor_tz if safe_zone(visitor_tz) else self.hours.tz_name,
            expects_reply=False, reply_kind="call_scheduled", audit=[*audit, "time_ok"],
        )


def all_wordings() -> dict[str, list[str]]:
    """Expose the wording table for validation tests."""
    return {key: list(values) for key, values in _WORDING.items()}
