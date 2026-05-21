"""GreetingIntentGuard — detects greeting-only messages that must not trigger scoping."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

_GREETING_ONLY_RE = re.compile(
    r"^(?:hello|hi|hey|good\s+(?:morning|afternoon|evening|day)|"
    r"greetings?|salam(?:ualaikum)?|salaam|howdy|yo|sup|hiya|"
    r"hola|bonjour|ciao|namaste|assalamu\s+alaikum)"
    r"(?:\s+(?:there|mate|friend|everyone|all|guys|folks|bookcraft|team))?"
    r"[!.,\s]*$",
    flags=re.IGNORECASE,
)

_GREETING_WORDS: frozenset[str] = frozenset(
    {
        "hello",
        "hi",
        "hey",
        "salam",
        "salaam",
        "greetings",
        "howdy",
        "hiya",
        "sup",
        "yo",
        "namaste",
    }
)

_SCOPING_RE = re.compile(
    r"\b(?:word\s+count|page\s+count|genre|manuscript|draft|story|book|"
    r"write|publish|edit|cover|price|cost|timeline|deadline|fiction|memoir|"
    r"service|marketing|audiobook|formatting)\b",
    flags=re.IGNORECASE,
)


class GreetingGuardResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_greeting_only: bool = False
    audit: list[str] = Field(default_factory=list)


def detect_greeting_only(text: str) -> GreetingGuardResult:
    """
    Detect whether a message is a greeting-only message.

    Greeting-only messages must not trigger scoping questions (genre, word count, etc.).
    Claude should write a welcome response; no hardcoded text here.
    """
    audit: list[str] = []
    stripped = text.strip()

    if _GREETING_ONLY_RE.match(stripped):
        audit.append("matched:greeting_pattern")
        return GreetingGuardResult(is_greeting_only=True, audit=audit)

    # Short message containing only greeting words (≤4 tokens)
    words = re.findall(r"\b[a-zA-Z']+\b", stripped.lower())
    if len(words) <= 4:
        has_greeting = any(w in _GREETING_WORDS for w in words)
        has_scoping = bool(_SCOPING_RE.search(stripped))
        if has_greeting and not has_scoping:
            audit.append("matched:short_greeting_no_scoping")
            return GreetingGuardResult(is_greeting_only=True, audit=audit)

    audit.append("not_greeting_only")
    return GreetingGuardResult(is_greeting_only=False, audit=audit)
