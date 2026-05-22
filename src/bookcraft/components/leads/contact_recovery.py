"""Contact recovery detectors.

Handles two critical user situations:

1. **Already-shared recovery** — user says "I already gave you that" when
   the bot is about to ask for contact details again.  If the thread state
   has full or partial contact info, the bot should acknowledge it, not
   re-ask.

2. **Complaint / privacy complaint recovery** — user says "what the hell
   you're sharing my contact details" when the bot (incorrectly) treated
   the customer's own PII as company contact info.  The bot must apologise,
   stop all sales/discovery questions, and re-establish trust.

Engines compute. Claude writes.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Already-shared patterns
# ---------------------------------------------------------------------------

_ALREADY_SHARED_RE = re.compile(
    r"\b(?:"
    r"already\s+shared|"
    r"already\s+gave|"
    r"already\s+provided|"
    r"already\s+given|"
    r"just\s+shared\s+it|"
    r"just\s+gave\s+(?:it\s+to\s+you|you)|"
    r"just\s+sent\s+it|"
    r"i\s+just\s+(?:gave|sent|provided|told|shared)|"
    r"shared\s+(?:it\s+)?above|"
    r"gave\s+it\s+above|"
    r"told\s+you\s+already|"
    r"i\s+told\s+you\s+(?:above|earlier|before|already)|"
    r"see\s+above|"
    r"read\s+above|"
    r"are\s+you\s+even\s+reading|"
    r"aren'?t\s+you\s+reading|"
    r"didn'?t\s+i\s+(?:just\s+)?(?:say|give|share|send|provide)|"
    r"i\s+already\s+(?:shared|gave|provided|sent|told)"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Complaint / privacy-violation patterns
# ---------------------------------------------------------------------------

_COMPLAINT_PRIVACY_RE = re.compile(
    r"\b(?:"
    r"(?:what\s+the\s+(?:fuck|hell|heck))|"
    r"are\s+you\s+(?:serious|kidding|even\s+reading)|"
    r"this\s+is\s+(?:annoying|ridiculous|wrong|insane)|"
    r"why\s+(?:are\s+you\s+(?:sharing|repeating|sending|giving)|"
    r"did\s+you\s+(?:share|repeat|send)|"
    r"is\s+(?:my|the)\s+(?:email|phone|contact|number)\s+(?:there|showing))|"
    r"(?:that'?s|those\s+are)\s+my\s+(?:contact\s+details?|email|phone|number|info(?:rmation)?)|"
    r"stop\s+(?:repeating|sharing|saying|echoing|showing)\s+(?:my|the)\s+"
    r"(?:contact|email|phone|number|info|details?)|"
    r"don'?t\s+(?:repeat|share|say|echo|show)\s+(?:my|the)\s+"
    r"(?:contact|email|phone|number|info|details?)|"
    r"you'?re\s+not\s+(?:listening|reading|paying\s+attention)|"
    r"privacy|"
    r"(?:my|that)\s+(?:is|was)\s+(?:my\s+)?(?:personal\s+)?(?:contact|email|phone|number|info)"
    r")\b",
    re.IGNORECASE,
)

# Specifically detects when the bot may have treated user PII as company contact.
_PII_MISUSE_RE = re.compile(
    r"\b(?:"
    r"those\s+(?:are|were)\s+my\s+(?:details?|contact|info|email|phone)|"
    r"that'?s?\s+(?:was\s+)?my\s+(?:email|phone|number|contact|info)|"
    r"that\s+was\s+my\s+(?:email|phone|number|contact|info|details?)|"
    r"you(?:'?re|\s+are)\s+(?:sharing|giving|revealing|repeating|sending)\s+my|"
    r"you\s+(?:were|are)\s+(?:giving|sharing)\s+(?:out|away)\s+my|"
    r"my\s+(?:contact\s+)?(?:details?|info(?:rmation)?)\s+(?:is|was|are|were)"
    r"\s+(?:not\s+)?(?:for|bookcraft|the\s+(?:company|team|bot))"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def user_claims_already_shared(text: str) -> bool:
    """Return True if the user says they already provided contact information.

    Used to prevent the bot from re-asking for details the user believes
    they have already given.
    """
    return bool(_ALREADY_SHARED_RE.search(text))


def user_has_complaint_or_privacy_concern(text: str) -> bool:
    """Return True if the message contains a complaint or privacy objection.

    Triggers trust-recovery mode: apologise, stop sales questions, correct
    any misuse of the user's PII.
    """
    return bool(_COMPLAINT_PRIVACY_RE.search(text)) or bool(_PII_MISUSE_RE.search(text))


def user_objects_to_pii_misuse(text: str) -> bool:
    """Return True specifically when the user calls out PII misuse/echo.

    Narrower than user_has_complaint_or_privacy_concern; use this to
    trigger the explicit PII-misuse apology path.
    """
    return bool(_PII_MISUSE_RE.search(text))
