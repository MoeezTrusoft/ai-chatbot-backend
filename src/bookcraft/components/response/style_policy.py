from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.response.planner import ResponsePlan

PREFERRED_OPENERS: list[str] = [
    "That gives us enough to",
    "The useful next step is",
    "Since your manuscript is finished",
    "Happy to help with that",
]

BANNED_OPENERS: list[str] = [
    "Sure!",
    "Absolutely!",
    "I can assist you with that.",
    "As an AI",
    "Thank you for reaching out.",
    "Great question!",
]

BANNED_PHRASES: list[str] = [
    "As an AI",
    "As your AI assistant",
    "I am an AI",
    "I'm an AI",
    "How can I assist you",
    "How can I help you",
]

WEAK_PHRASES: list[str] = [
    "maybe",
    "possibly",
    "I think",
    "I guess",
    "kind of",
    "sort of",
    "probably",
    "should be able to",
]

INTERNAL_TERMS: list[str] = [
    "backend",
    "classifier",
    "runtime atoms",
    "provider votes",
    "RAG",
    "tool_governance",
    "action_plan",
    "deterministic engine",
    "quote engine",
    "ContextArbiter",
]

SERVICE_GUIDANCE: dict[str, str] = {
    "cover_design_illustration": (
        "Ask visual direction or cover style. Do not ask draft status if already known."
    ),
    "ghostwriting": "Ask story stage, voice, length, or genre only if missing.",
    "editing_proofreading": "Ask manuscript length and type of edit.",
    "marketing_promotion": "Ask goal, platform, and timeline.",
    "publishing_distribution": "Ask target platforms and format.",
}

PRIMARY_GOAL_GUIDANCE: dict[str, str] = {
    "greeting_welcome": (
        "Welcome the author warmly and acknowledge what they shared. "
        "React to their project or situation first. "
        "Do NOT ask for name or contact details in the opening reply."
    ),
    "answer_current_question": (
        "Answer the author's actual question clearly and specifically. "
        "Move one natural step forward after answering. "
        "Do NOT lead with a contact ask."
    ),
    "continue_discovery": "Acknowledge context, then move one natural step forward.",
    "cover_design_scoping": (
        "Anchor on known manuscript status/genre and ask cover style direction."
    ),
    "pricing_scoping": "Gather only missing quote-critical scope details.",
    "consultation_scoping": "Propose booking next step and ask one scheduling detail.",
    "document_scoping": "Confirm the exact document and missing legal-safe details.",
    "portfolio_matching": "Ask one clarifier to provide relevant portfolio samples.",
    "lead_contact_capture": (
        "Ask for the author's name and one contact method (email or phone) — not both. "
        "Frame the ask around the benefit to the author, not the company's need."
    ),
    "consultation_handoff": (
        "Offer specialist consultation handoff and collect one contact channel."
    ),
    "specialist_handoff": "Confirm specialist handoff and collect required contact details.",
    "lead_created_confirmation": "Confirm handoff to a senior specialist and avoid more discovery.",
    "safe_blocked_action": "Do not imply completion. Explain safe next step only.",
    "clarify_intent": "Clarify the request before taking any action-oriented step.",
    # Gap 1: long-tail intent goals (mission alignment audit)
    "revision_response": (
        "Acknowledge the revision request clearly. Ask which version and what changes are needed. "
        "Offer a specialist review path. Do NOT ask scoping questions about word count or genre."
    ),
    "payment_guidance": (
        "Treat this as a late-funnel buying signal. Explain the booking or payment process "
        "simply. Offer a consultation to finalise. Do NOT ask scoping questions."
    ),
    "celebrate_and_advance": (
        "Celebrate the author's milestone warmly and specifically. "
        "Acknowledge the progress. Offer the natural next service step. No scoping questions."
    ),
    "complaint_recovery": (
        "Acknowledge the concern directly and empathetically. Do not dismiss or redirect. "
        "Offer a human or specialist consultation. Never ask a scoping question on this turn."
    ),
    "narrative_sharing": (
        "The author is telling you their story or personal history. Listen. "
        "Reflect back ONE specific, human detail they just shared, with genuine warmth. "
        "Do NOT ask a scoping question, pitch a consultation, quote pricing, or ask for "
        "contact details this turn. At most, ask ONE gentle, curious question about the "
        "story itself, or simply acknowledge and invite them to keep going."
    ),
    "gentle_clarify": (
        "Ask exactly one warm, open clarifying question. "
        "Do not attempt to scope the project or sell. Keep it brief and human."
    ),
    "minimal_acknowledge": (
        "Give a short, neutral acknowledgment. Do not engage the content. "
        "Do not ask scoping questions. Do not attempt to sell."
    ),
    "friendly_redirect": (
        "Acknowledge the off-topic message warmly. "
        "Redirect naturally to how BookCraft can help with their book project. "
        "Do NOT ask a scoping question."
    ),
}

_WEAK_PHRASE_LIMIT = 2
_QUESTION_MARK = "?"


class SalesToneReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    failures: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


class ResponseStylePolicy:
    def __init__(
        self,
        *,
        preferred_openers: list[str] | None = None,
        banned_openers: list[str] | None = None,
        banned_phrases: list[str] | None = None,
        weak_phrases: list[str] | None = None,
        internal_terms: list[str] | None = None,
        service_specific_guidance: dict[str, str] | None = None,
        primary_goal_guidance: dict[str, str] | None = None,
    ) -> None:
        self.preferred_openers = preferred_openers or PREFERRED_OPENERS
        self.banned_openers = banned_openers or BANNED_OPENERS
        self.banned_phrases = banned_phrases or BANNED_PHRASES
        self.weak_phrases = weak_phrases or WEAK_PHRASES
        self.internal_terms = internal_terms or INTERNAL_TERMS
        self.service_specific_guidance = service_specific_guidance or SERVICE_GUIDANCE
        self.primary_goal_guidance = primary_goal_guidance or PRIMARY_GOAL_GUIDANCE

        self._banned_opener_patterns = [
            re.compile(rf"^\s*{re.escape(opener)}", re.IGNORECASE) for opener in self.banned_openers
        ]
        self._banned_phrase_patterns = [
            re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE) for phrase in self.banned_phrases
        ]
        self._weak_phrase_patterns = [
            re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE) for phrase in self.weak_phrases
        ]
        self._internal_term_patterns = [
            re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in self.internal_terms
        ]

    @classmethod
    def default(cls) -> ResponseStylePolicy:
        return cls()

    def evaluate(
        self,
        *,
        text: str,
        response_plan: ResponsePlan | None = None,
        context_pack: ContextPack | None = None,
    ) -> SalesToneReport:
        failures: list[str] = []
        suggestions: list[str] = []
        audit: list[str] = []

        text_lower = text.lower()

        if any(pattern.search(text) for pattern in self._banned_opener_patterns):
            failures.append("banned_opener:generic_robotic")
            suggestions.append(
                "Start with a specific opener tied to user facts, then ask one next-step question."
            )
            audit.append("check:generic_robotic_opener:fail")
        else:
            audit.append("check:generic_robotic_opener:pass")

        if any(pattern.search(text) for pattern in self._banned_phrase_patterns):
            failures.append("robotic_phrase")
            suggestions.append("Remove generic helper phrasing and use consultative language.")
            audit.append("check:robotic_phrase:fail")
        else:
            audit.append("check:robotic_phrase:pass")

        if "super exciting" in text_lower or text.count("!") >= 3:
            failures.append("fake_excitement")
            suggestions.append("Use calm, professional language without hype punctuation.")
            audit.append("check:fake_excitement:fail")
        else:
            audit.append("check:fake_excitement:pass")

        if any(pattern.search(text) for pattern in self._internal_term_patterns):
            failures.append("internal_terms_detected")
            suggestions.append("Keep wording customer-safe and avoid internal system terms.")
            audit.append("check:internal_term_leak:fail")
        else:
            audit.append("check:internal_term_leak:pass")

        weak_hits = sum(len(pattern.findall(text)) for pattern in self._weak_phrase_patterns)
        if weak_hits > _WEAK_PHRASE_LIMIT:
            failures.append("excessive_weak_language")
            suggestions.append("Use confident wording and remove repeated hedging terms.")
            audit.append(f"check:excessive_weak_wording:fail:{weak_hits}")
        else:
            audit.append(f"check:excessive_weak_wording:pass:{weak_hits}")

        question_count = text.count(_QUESTION_MARK)
        if question_count > 1:
            failures.append("multiple_questions")
            failures.append("more_than_one_question")
            suggestions.append("Ask one clear next-step question in each response.")
            audit.append(f"check:more_than_one_question:fail:{question_count}")
        else:
            audit.append(f"check:more_than_one_question:pass:{question_count}")

        # Specificity: when the thread already knows concrete context (service, genre,
        # manuscript status, or captured facts), a reply must reference at least one of
        # them — a generic "can you share more details?" is a failure. Restored after an
        # incomplete refactor (17b03cd) left this as a no-op stub; it is NOT redundant
        # with repeated_known_fact_question below (that one only guards forbidden re-asks).
        if context_pack is not None and context_pack.known_facts:
            known_values = [str(fact.value).strip().lower() for fact in context_pack.known_facts]
            if context_pack.active_service:
                known_values.append(context_pack.active_service.replace("_", " ").lower())
            if context_pack.active_genre:
                known_values.append(context_pack.active_genre.lower())
            if context_pack.manuscript_status:
                known_values.append(context_pack.manuscript_status.replace("_", " ").lower())

            def _value_mentioned(value: str, body: str) -> bool:
                if not value or len(value) <= 2:  # noqa: PLR2004
                    return False
                if value in body:
                    return True
                words = [w for w in value.split() if len(w) > 3]  # noqa: PLR2004
                return bool(words) and any(w in body for w in words)

            if any(_value_mentioned(value, text_lower) for value in known_values):
                audit.append("check:missing_specificity_known_context:pass")
            else:
                failures.append("missing_specificity_known_context")
                suggestions.append(
                    "Reference at least one known fact (service, genre, or manuscript status)."
                )
                audit.append("check:missing_specificity_known_context:fail")
        else:
            audit.append("check:missing_specificity_known_context:skip")

        if context_pack is not None and context_pack.forbidden_reasks and _QUESTION_MARK in text:
            reask_hits = [
                forbidden
                for forbidden in context_pack.forbidden_reasks
                if forbidden and forbidden.lower() in text_lower
            ]
            if reask_hits:
                failures.append("repeated_known_fact_question")
                suggestions.append("Do not re-ask known details; ask for the next missing detail.")
                audit.append(f"check:repeated_known_fact_question:fail:{len(reask_hits)}")
            else:
                audit.append("check:repeated_known_fact_question:pass")
        else:
            audit.append("check:repeated_known_fact_question:skip")

        if response_plan is not None and response_plan.primary_goal == "safe_blocked_action":
            unsafe_claim_patterns = [
                re.compile(r"\bi (already )?(sent|completed|processed|booked|created|generated)\b"),
                re.compile(r"\b(done|completed) for you\b"),
            ]
            if any(pattern.search(text_lower) for pattern in unsafe_claim_patterns):
                failures.append("blocked_tool_unsafe_claim")
                suggestions.append("Do not claim completion; explain the safe next step clearly.")
                audit.append("check:blocked_tool_unsafe_claim:fail")
            else:
                audit.append("check:blocked_tool_unsafe_claim:pass")
        else:
            audit.append("check:blocked_tool_unsafe_claim:skip")

        if context_pack is not None and context_pack.active_service:
            guidance = self.service_specific_guidance.get(context_pack.active_service)
            if guidance:
                suggestions.append(f"service_guidance:{guidance}")
                audit.append(f"check:service_guidance:{context_pack.active_service}")

        if response_plan is not None:
            goal_guidance = self.primary_goal_guidance.get(response_plan.primary_goal)
            if goal_guidance:
                suggestions.append(f"primary_goal_guidance:{goal_guidance}")
                audit.append(f"check:primary_goal_guidance:{response_plan.primary_goal}")

        passed = len(failures) == 0
        audit.append(f"result:passed={passed}:failures={len(failures)}")
        return SalesToneReport(
            passed=passed,
            failures=failures,
            suggestions=suggestions,
            audit=audit,
        )

    def style_instructions(self, *, active_service: str | None = None) -> str:
        guidance = ""
        if active_service:
            service_guidance = self.service_specific_guidance.get(active_service)
            if service_guidance:
                guidance = f"\nService guidance: {service_guidance}"
        return (
            "Tone: warm, specific, human, consultative, concise.\n"
            "Approach: react to what the author shared, then move ONE natural step forward.\n"
            "On a first message — welcome and engage; never lead with a contact ask.\n"
            "When the author asks a question — answer it fully before anything else.\n"
            "After deflecting a contact ask — add value; do not repeat the same ask.\n"
            "Moving forward is not always a question and not always a contact ask. "
            "Sometimes it is an answer, a next option, or a soft invitation.\n"
            "If you must ask for contact, ask for name + ONE channel (email or phone), "
            "framed around the author's benefit.\n"
            f"Avoid openers like: {', '.join(self.banned_openers)}.\n"
            "Avoid fake excitement, hype, and excessive exclamation marks.\n"
            f"Avoid weak wording: {', '.join(self.weak_phrases)}.\n"
            "Do not expose internal terms or blocked-action completion claims."
            f"{guidance}"
        )
