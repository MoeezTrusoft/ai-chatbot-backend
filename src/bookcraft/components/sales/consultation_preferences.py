"""ConsultationPreferenceDetector.

Detects two customer signals that the consultation flow previously could not
represent at all, and therefore steamrolled (chat 6816):

1. **Call opt-out** — "can they text i'm really bad at calling", "can we text
   instead", "can he text me please". The customer HAS a phone and is happy to be
   reached on it; they just don't want a voice call. The existing
   ``ContactAvailabilityDetector`` only models a channel being *unavailable*
   ("my phone is unable to be used"), which is a different thing: it stops us
   soliciting the number. Here the number is fine — what must stop is the
   consultation *call* booking loop. The customer asked to be texted four
   separate times and the bot kept asking which hour to call.

2. **Deferral** — "okay so we might need to do it next month", "but I'm not doing
   it until next month". The stage machine only moves toward a booking; it had no
   way to record "not yet", so it re-opened the day/time ask on the very next turn.

Deliberately conservative, mirroring ``contact_availability``: a false negative
just keeps the normal flow, while a false positive silently kills a live booking.
Deferral in particular requires an explicit postponement cue — a bare "next
month" ("my book launches next month") must NOT read as a deferral.

Engines compute. Claude writes final customer-facing text.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Call opt-out — prefers text/SMS over a voice call
# ---------------------------------------------------------------------------

# "can they text", "can he text me please", "text me instead", "please text",
# "prefer texting", "rather text", "i'm bad at calling", "don't like phone calls",
# "can we do this over text".
_CALL_OPT_OUT_RE = re.compile(
    r"(?:"
    # Asking to be texted.
    r"(?:can|could|would|will)\s+(?:you|he|she|they|we|someone|the\s+specialist)\s+"
    r"(?:just\s+|please\s+)?(?:text|sms|message|msg)\b|"
    r"(?:can|could)\s+(?:we|i)\s+(?:just\s+)?(?:do\s+(?:it|this)\s+)?(?:over|via|by|through)\s+(?:text|sms|message)|"
    r"(?:please\s+)?(?:just\s+)?text\s+(?:me|us)\b|"
    r"(?:text|sms)\s+(?:me|us)?\s*instead\b|"
    r"if\s+you\s+text\s+me\b|"
    # Stating a preference for text.
    r"(?:prefer|rather|better)\s+(?:to\s+)?(?:be\s+)?(?:text(?:ed|ing)?|sms|messag(?:e|ed|ing))\b|"
    r"(?:text(?:ing)?|sms)\s+(?:is|works)\s+(?:better|best|fine|good|easier)\b|"
    # Aversion to calls.
    r"(?:bad|terrible|awful|not\s+good|no\s+good)\s+at\s+(?:calling|phone\s+calls?|talking\s+on\s+the\s+phone)|"
    r"(?:don'?t|do\s+not|dont)\s+(?:like|enjoy|want|do)\s+(?:phone\s+)?calls?\b|"
    r"(?:hate|avoid|anxious\s+about|nervous\s+about)\s+(?:phone\s+)?calls?\b|"
    r"(?:don'?t|do\s+not|would\s+rather\s+not)\s+(?:want\s+to\s+)?(?:talk|speak)\s+on\s+the\s+phone|"
    r"(?:no|not\s+a)\s+(?:phone\s+)?calls?\s+please"
    r")",
    re.IGNORECASE,
)

# The customer explicitly (re-)asks for a call — cancels a prior opt-out.
_CALL_OPT_IN_RE = re.compile(
    r"(?:"
    r"(?:you|they|he|she|someone)\s+can\s+(?:just\s+)?call\s+me|"
    r"(?:please\s+)?call\s+me\s+(?:instead|please)|"
    r"(?:a\s+)?call\s+(?:is|works)\s+(?:fine|better|best|good|ok)|"
    r"(?:prefer|rather)\s+(?:a\s+)?(?:phone\s+)?call\b|"
    r"happy\s+to\s+(?:talk|speak|jump)\s+on\s+(?:a\s+)?call"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Deferral — wants to postpone the engagement / consultation
# ---------------------------------------------------------------------------

# A future-time reference the deferral cue must attach to.
_DEFER_HORIZON = (
    r"next\s+(?:month|year|week)|"
    r"(?:in|after)\s+(?:a\s+)?(?:few|couple(?:\s+of)?|several|\d+)\s+(?:days?|weeks?|months?)|"
    r"later\s+(?:this\s+)?(?:month|year|on)|"
    r"(?:the\s+)?(?:end|start|beginning)\s+of\s+(?:the\s+)?(?:month|year)|"
    r"after\s+(?:the\s+)?(?:holidays?|new\s+year|summer)"
)

# Explicit postponement. Requires BOTH a postponement cue and a horizon (or an
# unambiguous "not right now"), so "my book comes out next month" is not a
# deferral while "we might need to do it next month" is.
_DEFER_RE = re.compile(
    r"(?:"
    # "not doing it until next month", "can't do it until next month"
    rf"(?:not|can'?t|cannot|won'?t)\s+(?:be\s+)?(?:doing|do|start|starting|book|booking|going)?\s*"
    rf"(?:it|this|that)?\s*(?:until|till|til)\s+(?:{_DEFER_HORIZON})|"
    # "might need to do it next month", "we'll do it next month", "let's do it next month"
    rf"(?:might|may|maybe|probably|we'?ll|i'?ll|let'?s|going\s+to|gonna|need\s+to|have\s+to|want\s+to)\s+"
    rf"(?:need\s+to\s+|have\s+to\s+|wait\s+(?:and\s+)?)?"
    rf"(?:do|start|book|schedule|revisit|circle\s+back|touch\s+base|reconnect|proceed|move\s+forward)\s+"
    rf"(?:it|this|that|back)?\s*(?:in\s+|on\s+)?(?:{_DEFER_HORIZON})|"
    # "hold off until", "wait until", "push it to next month", "postpone"
    rf"(?:hold\s+off|wait|hold\s+on|push\s+(?:it|this)?\s*(?:back|out|to)?|postpone|defer|delay)\s+"
    rf"(?:until|till|til|to|for)?\s*(?:{_DEFER_HORIZON})|"
    # Bare "not right now" style — unambiguous without a horizon.
    r"not\s+(?:right\s+now|at\s+the\s+moment|yet|just\s+yet|ready\s+(?:yet|right\s+now))|"
    r"(?:maybe|perhaps)\s+(?:some\s+)?other\s+time|"
    r"(?:i'?m\s+)?not\s+(?:doing|booking|scheduling)\s+(?:it|this|anything)\s+(?:yet|now)"
    r")",
    re.IGNORECASE,
)

# The customer re-engages after deferring — cancels a prior deferral.
_DEFER_CANCEL_RE = re.compile(
    r"(?:"
    r"(?:let'?s|lets|can\s+we|i\s+want\s+to|i'?d\s+like\s+to)\s+"
    r"(?:go\s+ahead|proceed|do\s+it|book\s+(?:it|now)|schedule\s+(?:it|now)|start)\b|"
    r"(?:i'?m\s+)?ready\s+(?:to\s+(?:go|start|book|proceed)|now)\b|"
    r"book\s+(?:it|me)\s+(?:now|today)\b|"
    r"changed\s+my\s+mind"
    r")",
    re.IGNORECASE,
)


class ConsultationPreferenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_opt_out: bool = False
    call_opt_in: bool = False
    deferred: bool = False
    defer_cancelled: bool = False
    defer_hint: str | None = None
    audit: list[str] = Field(default_factory=list)


class ConsultationPreferenceDetector:
    """Detects call-modality opt-out and consultation deferral."""

    def detect(self, text: str) -> ConsultationPreferenceResult:
        audit: list[str] = []
        stripped = (text or "").strip()
        if not stripped:
            return ConsultationPreferenceResult(audit=["empty"])

        call_opt_out = bool(_CALL_OPT_OUT_RE.search(stripped))
        call_opt_in = bool(_CALL_OPT_IN_RE.search(stripped))
        deferred = bool(_DEFER_RE.search(stripped))
        defer_cancelled = bool(_DEFER_CANCEL_RE.search(stripped))

        # A message that both asks for a call and refuses one is ambiguous —
        # record neither rather than guessing (mirrors ContactAvailabilityDetector).
        if call_opt_out and call_opt_in:
            audit.append("ambiguous_call_preference_ignored")
            call_opt_out = call_opt_in = False

        # An explicit "let's book it now" outranks a deferral phrase in the same
        # breath ("not right now — actually, let's just book it").
        if deferred and defer_cancelled:
            audit.append("defer_cancelled_wins")
            deferred = False

        defer_hint: str | None = None
        if deferred:
            m = _DEFER_RE.search(stripped)
            if m:
                defer_hint = m.group(0).strip()

        for flag, label in (
            (call_opt_out, "call_opt_out"),
            (call_opt_in, "call_opt_in"),
            (deferred, "deferred"),
            (defer_cancelled, "defer_cancelled"),
        ):
            if flag:
                audit.append(label)

        return ConsultationPreferenceResult(
            call_opt_out=call_opt_out,
            call_opt_in=call_opt_in,
            deferred=deferred,
            defer_cancelled=defer_cancelled,
            defer_hint=defer_hint,
            audit=audit,
        )
