"""InputSafetyGuard — severity-accurate input safety classification.

Distinguishes casual frustration from directed abuse, threats, and hate speech.
Blocked turns skip Claude, tools, and lead creation entirely.
Engines compute. Claude writes normal customer-facing text.
Blocked turns return a system UI message, not assistant prose.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Threats of physical harm directed at the team / bot / any person.
_THREAT_RE = re.compile(
    r"\b(?:i(?:'ll|\s+will|\s+am\s+going\s+to|\s+gonna)\s+"
    r"(?:hurt|kill|destroy|attack|come\s+for|find|harm|beat|shoot|stab|end)\b|"
    r"you(?:'ll|\s+will)\s+(?:regret|pay|suffer|die|be\s+sorry)\b|"
    r"watch\s+(?:your\s+back|out)\b|"
    r"i\s+know\s+where\s+you\b|"
    r"your\s+(?:team|people|staff|employees?)\s+(?:will|are\s+going\s+to)\s+"
    r"(?:regret|pay|suffer|be\s+sorry)\b)",
    re.IGNORECASE,
)

# Directed insults — profanity or slurs aimed at the bot/team/person.
# Key: must be directed ("you are …", "what a … you are") not situational.
_DIRECTED_INSULT_RE = re.compile(
    r"\b(?:you(?:\s+(?:are|'re|fucking|absolute))+\s+"
    r"(?:stupid|idiot|moron|imbecile|useless|worthless|piece\s+of\s+shit|asshole|"
    r"bitch|bastard|dumbass|retard|cunt|twat|dipshit|fuckwit)|"
    r"what\s+(?:a|an)\s+(?:stupid|useless|piece\s+of\s+shit|waste\s+of)\s+\w+\s+you\s+are|"
    r"(?:stupid|idiot|moron|imbecile|useless|worthless)\s+(?:bot|ai|robot|chatbot|system|service|company|team))\b",
    re.IGNORECASE,
)

# Hate speech — slurs targeting identity groups.
# Intentionally sparse to avoid false positives; targets unambiguous slurs only.
_HATE_RE = re.compile(
    r"\b(?:nigger|nigga|chink|spic|kike|faggot|tranny|wetback|gook|raghead|"
    r"sandnigger|towelhead|cracker\s+ass|white\s+trash|die\s+(?:jew|muslim|christian|"
    r"black|white|gay|lesbian|trans))\b",
    re.IGNORECASE,
)

# Sexual abuse / explicit sexual aggression directed at the system/team.
_SEXUAL_ABUSE_RE = re.compile(
    r"\b(?:i(?:'ll|\s+will|\s+am\s+going\s+to)\s+(?:rape|molest|sexually\s+assault)|"
    r"go\s+fuck\s+yourself|fuck\s+you\s+and\s+your\s+(?:team|company|bot|ai)|"
    r"suck\s+my\s+(?:dick|cock|ass)|eat\s+shit\s+and\s+die)\b",
    re.IGNORECASE,
)

# Casual profanity / situational frustration — NOT directed at anyone.
# These should result in warn or allow, never block.
_CASUAL_PROFANITY_RE = re.compile(
    r"\b(?:this\s+is\s+(?:fucking|f\*cking|fu\*king|damn|bloody|so)\s+"
    r"(?:confusing|annoying|frustrating|hard|difficult|unclear|complicated|broken|useless)|"
    r"what\s+the\s+(?:fuck|hell|heck|f\*ck)\b|"
    r"holy\s+(?:shit|crap|cow)\b|"
    r"(?:damn|crap|shit|f\*ck|hell)\s+(?:it|this|that)(?:\b|$)|"
    r"for\s+(?:fuck|f\*ck|heaven|god|goodness)\s+sake|"
    r"are\s+you\s+(?:serious|kidding)\??\s*(?:!|\b))\b",
    re.IGNORECASE,
)

# Normal complaint — frustration about service, price, wait time, etc.
_NORMAL_COMPLAINT_RE = re.compile(
    r"\b(?:this\s+is\s+(?:too\s+)?(?:expensive|pricey|slow|bad|wrong|not\s+what\s+i\s+wanted)|"
    r"i'?m\s+(?:unhappy|disappointed|frustrated|not\s+satisfied)|"
    r"why\s+(?:is\s+this|does\s+this|do\s+you)|"
    r"not\s+(?:helpful|useful|what\s+i\s+need|working)|"
    r"terrible\s+(?:service|support|experience)|"
    r"i\s+want\s+to\s+(?:complain|speak\s+to|talk\s+to)\b)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Safety event helpers
# ---------------------------------------------------------------------------

_RECENT_TURNS_WINDOW = 5  # how many recent events to consider for escalation


def _recent_hostility_count(state: Any) -> int:
    """Count warn/block events in the last N safety events."""
    if state is None:
        return 0
    events: list[dict[str, Any]] = getattr(state, "safety_events", None) or []
    count = 0
    for event in events[-_RECENT_TURNS_WINDOW:]:
        if isinstance(event, dict) and event.get("action") in {"warn", "block"}:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class InputSafetyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["allow", "warn", "block"]
    reason: str
    severity: int = 0  # 0=clean, 1=mild, 2=warn, 3=block, 4=severe-block
    input_disabled: bool = False
    system_message: str | None = None
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

_SYSTEM_MESSAGE_THREAT = (
    "For the safety of our team this conversation has ended. "
    "Please reach out through official support channels if you need assistance."
)
_SYSTEM_MESSAGE_BLOCK = (
    "This conversation has been paused due to the nature of the messages. "
    "BookCraft support is available through official channels."
)
_SYSTEM_MESSAGE_ESCALATED = (
    "This conversation has been paused after repeated issues. "
    "Please contact BookCraft support directly."
)


class InputSafetyGuard:
    """
    Classifies incoming messages for safety severity.

    action=allow  → message proceeds through the full pipeline.
    action=warn   → message proceeds; event is recorded for escalation tracking.
    action=block  → message is rejected; no Claude, no tools, no lead creation.
    """

    def evaluate(
        self,
        message: str,
        state: Any | None = None,
    ) -> InputSafetyDecision:
        audit: list[str] = []
        recent = _recent_hostility_count(state)
        audit.append(f"recent_hostility:{recent}")

        # Check escalation first — repeated hostility → block regardless of current message.
        if recent >= 3:
            audit.append("escalation:repeated_hostility")
            return InputSafetyDecision(
                action="block",
                reason="Repeated hostile messages in this conversation.",
                severity=3,
                input_disabled=True,
                system_message=_SYSTEM_MESSAGE_ESCALATED,
                audit=audit,
            )

        # Threat of physical harm — highest severity.
        if _THREAT_RE.search(message):
            audit.append("pattern:threat")
            return InputSafetyDecision(
                action="block",
                reason="Message contains a threat of harm.",
                severity=4,
                input_disabled=True,
                system_message=_SYSTEM_MESSAGE_THREAT,
                audit=audit,
            )

        # Hate speech / identity attacks.
        if _HATE_RE.search(message):
            audit.append("pattern:hate_speech")
            return InputSafetyDecision(
                action="block",
                reason="Message contains hate speech or identity attack.",
                severity=4,
                input_disabled=True,
                system_message=_SYSTEM_MESSAGE_BLOCK,
                audit=audit,
            )

        # Sexual abuse / extreme directed aggression.
        if _SEXUAL_ABUSE_RE.search(message):
            audit.append("pattern:sexual_abuse")
            return InputSafetyDecision(
                action="block",
                reason="Message contains sexual abuse or extreme directed aggression.",
                severity=4,
                input_disabled=True,
                system_message=_SYSTEM_MESSAGE_BLOCK,
                audit=audit,
            )

        # Directed insult at the bot / team.
        if _DIRECTED_INSULT_RE.search(message):
            audit.append("pattern:directed_insult")
            return InputSafetyDecision(
                action="block",
                reason="Message contains a directed personal insult.",
                severity=3,
                input_disabled=False,
                system_message=_SYSTEM_MESSAGE_BLOCK,
                audit=audit,
            )

        # Casual profanity / situational frustration — not directed at anyone.
        if _CASUAL_PROFANITY_RE.search(message):
            audit.append("pattern:casual_profanity")
            return InputSafetyDecision(
                action="warn",
                reason="Message contains casual profanity expressing frustration.",
                severity=2,
                input_disabled=False,
                system_message=None,
                audit=audit,
            )

        # Normal complaint — allow as-is.
        if _NORMAL_COMPLAINT_RE.search(message):
            audit.append("pattern:normal_complaint")
            return InputSafetyDecision(
                action="allow",
                reason="Normal customer complaint — no safety concern.",
                severity=0,
                audit=audit,
            )

        # Escalation zone: recent hostility >= 2 → warn even on ambiguous messages.
        if recent >= 2:
            audit.append("escalation:pre_block_warning")
            return InputSafetyDecision(
                action="warn",
                reason="Prior hostile messages noted; monitoring this conversation.",
                severity=2,
                audit=audit,
            )

        audit.append("clean")
        return InputSafetyDecision(
            action="allow",
            reason="No safety concern detected.",
            severity=0,
            audit=audit,
        )

    @staticmethod
    def build_safety_event(
        message: str,
        decision: InputSafetyDecision,
    ) -> dict[str, Any]:
        """Return a safety event dict for appending to state.safety_events."""
        return {
            "action": decision.action,
            "severity": decision.severity,
            "reason": decision.reason,
            "message_preview": message[:80],
            "recorded_at": datetime.now(UTC).isoformat(),
        }
