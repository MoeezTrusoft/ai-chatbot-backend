"""ContactAvailabilityDetector.

Detects when a customer states that a contact channel is *unavailable* — they
can't or won't provide it — as opposed to simply not having given it yet. This is
the signal that drives the ``unavailable`` contact status: once a field is
unavailable, the bot stops soliciting it and a consultation proceeds on whatever
channel remains (chat 6759: "unfortunately currently my phone is unable to be
used. my main source of contact is my email" — the bot kept demanding a phone).

Deliberately conservative (high precision): a false negative just keeps the
normal "keep asking" flow, but a false positive would silently drop a field we
should still request. Only fires on explicit unavailability statements, never on
a neutral "I'll give it later".

Engines compute. Claude writes final customer-facing text.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

# Phone declared unavailable. Covers "don't have a phone", "no phone (number)",
# "phone is unable to be used / not working / broken / disconnected / dead",
# "can't/cannot use my phone", "no way to take calls", and "email only / email is
# my (main/best/only) contact" (which implies the phone channel is out).
_PHONE_UNAVAILABLE_RE = re.compile(
    r"(?:"
    r"(?:don'?t|do\s+not|doesn'?t|does\s+not)\s+have\s+(?:a\s+)?(?:phone|cell|mobile|number)|"
    r"(?:no|without)\s+(?:phone|cell|mobile)(?:\s+(?:number|access))?\b|"
    r"(?:phone|cell|mobile|number)\s+(?:is\s+|isn'?t\s+|is\s+not\s+)?"
    r"(?:unable\s+to\s+be\s+used|not\s+(?:working|available|usable|in\s+service)|"
    r"unavailable|out\s+of\s+service|disconnected|broken|dead|down)|"
    r"(?:can'?t|cannot|can\s+not|unable\s+to)\s+(?:use|access|receive\s+calls?\s+on)\s+(?:my\s+)?(?:phone|cell|mobile)|"
    r"(?:can'?t|cannot|can\s+not|unable\s+to)\s+(?:take|receive|get)\s+(?:phone\s+)?calls?|"
    r"(?:no\s+way\s+to|can'?t)\s+(?:be\s+)?(?:called|reached\s+by\s+phone)|"
    r"email\s+(?:is\s+)?(?:my\s+)?(?:main|best|only|primary|preferred)\s+(?:source\s+of\s+)?(?:contact|way)|"
    r"(?:only|just|prefer)\s+(?:by\s+|through\s+|via\s+)?email|"
    r"only\s+(?:be\s+)?(?:reach|contact)(?:ed)?\s+(?:me\s+)?(?:by|through|via|on)\s+(?:my\s+)?email|"
    r"reach\s+me\s+(?:by\s+|through\s+|via\s+|on\s+)?(?:my\s+)?email\s+only"
    r")",
    re.IGNORECASE,
)

# Email declared unavailable — much rarer, but symmetric. "don't have an email",
# "no email", "phone only / call me only".
_EMAIL_UNAVAILABLE_RE = re.compile(
    r"(?:"
    r"(?:don'?t|do\s+not|doesn'?t|does\s+not)\s+have\s+(?:an?\s+)?email|"
    r"(?:no|without)\s+email(?:\s+(?:address|account))?\b|"
    r"(?:phone|call)\s+(?:is\s+)?(?:my\s+)?(?:main|best|only|primary|preferred)\s+(?:source\s+of\s+)?(?:contact|way)|"
    r"(?:only|just|prefer)\s+(?:by\s+|through\s+|via\s+)?(?:phone|call)"
    r")",
    re.IGNORECASE,
)


class ContactAvailabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_unavailable: bool = False
    email_unavailable: bool = False
    audit: list[str] = Field(default_factory=list)


class ContactAvailabilityDetector:
    """Detects explicit "I can't provide this contact channel" statements."""

    def detect(self, text: str) -> ContactAvailabilityResult:
        audit: list[str] = []
        stripped = (text or "").strip()
        if not stripped:
            return ContactAvailabilityResult(audit=["empty"])

        phone_unavailable = bool(_PHONE_UNAVAILABLE_RE.search(stripped))
        email_unavailable = bool(_EMAIL_UNAVAILABLE_RE.search(stripped))

        # Guard: "email only" trips both the phone-unavailable ("email is my only
        # contact") and — via a loose read — must NOT also mark email unavailable.
        # The two patterns are mutually exclusive by construction, but if a message
        # somehow trips both, treat it as ambiguous and record neither so we don't
        # strand the customer with no reachable channel.
        if phone_unavailable and email_unavailable:
            audit.append("ambiguous_both_unavailable_ignored")
            return ContactAvailabilityResult(audit=audit)

        if phone_unavailable:
            audit.append("phone_unavailable")
        if email_unavailable:
            audit.append("email_unavailable")
        return ContactAvailabilityResult(
            phone_unavailable=phone_unavailable,
            email_unavailable=email_unavailable,
            audit=audit,
        )
