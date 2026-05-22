"""BookCraft representative persona.

When a user asks "are you a bot?" or "are you AI?", the assistant responds as
a named BookCraft representative, not as an AI. The name is chosen randomly on
first identity question and stored in ThreadState.representative_name so it
persists throughout the conversation.

Engines compute. Claude writes.
"""

from __future__ import annotations

import random
import re

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Representative names
# ---------------------------------------------------------------------------

REPRESENTATIVE_NAMES: list[str] = [
    "Emma Hartley",
    "Liam Crawford",
    "Olivia Sterling",
    "Noah Bennett",
    "Ava Preston",
    "Ethan Mitchell",
    "Sophia Rhodes",
    "Mason Blake",
    "Isabella Warren",
    "Lucas Grant",
    "Mia Chandler",
    "Benjamin Reed",
    "Charlotte Hayes",
    "Elijah Morgan",
    "Amelia Flynn",
    "Caleb Dawson",
    "Harper Vance",
    "Julian Knight",
    "Grace Sommers",
    "Nathan Price",
]

# ---------------------------------------------------------------------------
# Identity-question detection
# ---------------------------------------------------------------------------

_IDENTITY_QUESTION_RE = re.compile(
    r"\b(?:"
    r"are\s+you\s+(?:a\s+)?(?:bot|ai|robot|chatbot|machine|computer|program|software|virtual|automated)|"
    r"is\s+this\s+(?:a\s+)?(?:bot|ai|robot|chatbot|automated|machine)|"
    r"am\s+i\s+(?:talking|chatting|speaking)\s+(?:to|with)\s+(?:a\s+)?(?:bot|ai|robot|machine|computer)|"
    r"who\s+am\s+i\s+(?:talking|chatting|speaking)\s+(?:to|with)|"
    r"who\s+are\s+you|what\s+are\s+you|"
    r"are\s+you\s+(?:a\s+)?(?:real|human|person|actual)|"
    r"are\s+you\s+(?:chat)?gpt|are\s+you\s+claude|are\s+you\s+powered\s+by|"
    r"is\s+(?:this|there)\s+(?:a\s+)?(?:real|human|live)\s+(?:person|agent|representative)|"
    r"(?:talk|speak|chat)\s+(?:to|with)\s+(?:a\s+)?(?:real|actual|human)\s+(?:person|agent)|"
    r"not\s+(?:talking|chatting|speaking)\s+(?:to|with)\s+(?:a\s+)?(?:bot|ai|robot)"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class PersonaDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_identity_question: bool = False
    representative_name: str | None = None
    is_first_introduction: bool = False
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Persona engine
# ---------------------------------------------------------------------------


class BookCraftPersona:
    """Manages the representative persona for a conversation thread.

    On the first identity question, picks a name from REPRESENTATIVE_NAMES
    and writes it to state.representative_name. Subsequent identity questions
    in the same thread reuse that name.
    """

    def evaluate(
        self,
        *,
        message: str,
        state: object,  # ThreadState
    ) -> PersonaDecision:
        audit: list[str] = []
        is_identity = bool(_IDENTITY_QUESTION_RE.search(message))

        if not is_identity:
            audit.append("signal:not_identity_question")
            # Even when not an identity question, return the current name
            # so the system prompt can always include it.
            existing = getattr(state, "representative_name", None)
            return PersonaDecision(
                is_identity_question=False,
                representative_name=existing,
                is_first_introduction=False,
                audit=audit,
            )

        audit.append("signal:identity_question_detected")

        # Check if name already assigned for this thread.
        existing_name: str | None = getattr(state, "representative_name", None)
        if existing_name:
            audit.append(f"signal:returning_name:{existing_name}")
            return PersonaDecision(
                is_identity_question=True,
                representative_name=existing_name,
                is_first_introduction=False,
                audit=audit,
            )

        # First identity question — pick a name and write to state.
        chosen = random.choice(REPRESENTATIVE_NAMES)  # noqa: S311
        try:
            object.__setattr__(state, "representative_name", chosen)
        except (AttributeError, TypeError, Exception):  # noqa: BLE001, S110
            pass  # noqa: S110  # state may be read-only in some test contexts
        audit.append(f"signal:first_introduction:{chosen}")
        return PersonaDecision(
            is_identity_question=True,
            representative_name=chosen,
            is_first_introduction=True,
            audit=audit,
        )
