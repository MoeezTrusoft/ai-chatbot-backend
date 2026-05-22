"""Complaint classifier for BookCraft chatbot.

Batch 4: detects complaint and frustration signals in user messages and converts
them into a structured `ComplaintClassification` that the response planner can use
to enter a recovery posture instead of continuing the sales script.

Categories:
  privacy_complaint     — user objects to PII handling or echo
  repeated_question     — user says they already answered this
  not_answering         — user says the bot is not addressing their actual question
  wrong_service         — user corrects the active service focus
  price_objection       — user objects to pricing or pushes for guarantees
  human_handoff_request — user wants to talk to a person
  general_frustration   — expressions of annoyance or dissatisfaction
  abusive_or_threatening — hostile or abusive language (handled by safety guard)

Engines compute. Claude writes.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ComplaintCategory(StrEnum):
    PRIVACY_COMPLAINT = "privacy_complaint"
    REPEATED_QUESTION = "repeated_question"
    NOT_ANSWERING = "not_answering"
    WRONG_SERVICE = "wrong_service"
    PRICE_OBJECTION = "price_objection"
    HUMAN_HANDOFF_REQUEST = "human_handoff_request"
    GENERAL_FRUSTRATION = "general_frustration"
    ABUSIVE_OR_THREATENING = "abusive_or_threatening"


class ComplaintSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ComplaintClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected: bool = False
    category: ComplaintCategory | None = None
    severity: ComplaintSeverity = ComplaintSeverity.LOW
    should_stop_sales_script: bool = False
    should_apologize: bool = False
    should_offer_handoff: bool = False
    recovery_goal: str | None = None
    forbidden_questions: list[str] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

_PRIVACY_RE = re.compile(
    r"\b(?:"
    r"that'?s?\s+(?:was\s+)?my\s+(?:email|phone|number|contact|info(?:rmation)?)|"
    r"those\s+(?:are|were)\s+my\s+(?:details?|contact|info)|"
    r"you(?:'?re|\s+are)\s+(?:sharing|giving|revealing|repeating|sending)\s+my|"
    r"stop\s+(?:repeating|sharing|saying|echoing|showing)\s+(?:my|the)\s+"
    r"(?:contact|email|phone|number|info|details?)|"
    r"don'?t\s+(?:repeat|share|say|echo|show)\s+(?:my|the)\s+"
    r"(?:contact|email|phone|number|info|details?)|"
    r"privacy\s+(?:issue|concern|violation|problem)"
    r")\b",
    re.IGNORECASE,
)

_REPEATED_QUESTION_RE = re.compile(
    r"\b(?:"
    r"already\s+(?:told|said|shared|gave|provided|answered|mentioned)|"
    r"i\s+(?:just\s+)?(?:told|said|shared|gave|provided|answered)\s+(?:you|that)|"
    r"(?:told|said|shared|gave|provided)\s+(?:you\s+)?(?:that\s+)?already|"
    r"you(?:'?re|\s+are)\s+(?:not\s+)?(?:reading|listening|paying\s+attention)|"
    r"(?:didn'?t|haven'?t)\s+i\s+(?:just\s+)?(?:say|give|share|send|provide|tell\s+you)|"
    r"i\s+already\s+(?:shared|gave|provided|sent|told)|"
    r"are\s+you\s+even\s+(?:reading|listening)|"
    r"see\s+above|read\s+(?:what\s+i\s+)?(?:wrote|said|shared)"
    r")\b",
    re.IGNORECASE,
)

_NOT_ANSWERING_RE = re.compile(
    r"\b(?:"
    # Strong "you're not answering" patterns — require explicit blame on the bot.
    r"you(?:'?re|\s+are)\s+not\s+(?:answering|addressing|getting\s+it)|"
    r"you(?:'?re|\s+are)\s+(?:off\s+topic|missing\s+(?:the\s+)?point|not\s+getting\s+it)|"
    # "That's not what I asked" — user correcting a wrong answer (requires "that/this" pronoun
    # pointing back at a prior bot response, not just a user preference statement).
    r"that'?s?\s+not\s+(?:what\s+i\s+(?:asked|said|meant)|answering\s+my\s+question)|"
    # Explicit "didn't answer my question" phrasing.
    r"(?:didn'?t\s+)?answer\s+(?:my|the)\s+(?:actual\s+)?question"
    r")\b",
    re.IGNORECASE,
)

_WRONG_SERVICE_RE = re.compile(
    r"\b(?:"
    # "I asked about X, not Y" — correction of an existing wrong bot focus.
    r"i\s+(?:asked|said)\s+(?:about\s+)?(?:"
    r"(?:editing|ghostwriting|cover|formatting|publishing|distribution|marketing|"
    r"audiobook|website|video|trailer))\s*,\s*not\s+(?:editing|ghostwriting|cover|"
    r"formatting|publishing|distribution|marketing|audiobook|website|video|trailer)|"
    r"wrong\s+service|different\s+service"
    r")\b",
    re.IGNORECASE,
)

_PRICE_OBJECTION_RE = re.compile(
    r"\b(?:"
    r"too\s+(?:expensive|costly|much)|"
    r"can'?t\s+afford|out\s+of\s+(?:my\s+)?budget|"
    r"guarantee\s+(?:me|you|the|a)|"
    r"money\s+back|refund\s+(?:policy|guarantee)|"
    r"(?:that'?s\s+a\s+)?rip[\s-]?off|"
    r"cheaper\s+(?:elsewhere|online|alternative)|"
    r"why\s+(?:is\s+it\s+so|are\s+(?:your\s+)?prices)\s+(?:high|expensive)"
    r")\b",
    re.IGNORECASE,
)

_HUMAN_HANDOFF_RE = re.compile(
    r"\b(?:"
    r"(?:talk|speak|chat)\s+(?:to|with)\s+(?:a\s+)?(?:human|person|real\s+person|agent|"
    r"someone|specialist|representative|rep)|"
    r"connect\s+(?:me\s+)?(?:to|with)\s+(?:a\s+)?(?:human|person|agent|someone)|"
    r"(?:get|want)\s+(?:a\s+)?human|"
    r"stop\s+(?:talking|chatting)\s+to\s+(?:the\s+)?bot|"
    r"(?:i\s+want|i'?d\s+like)\s+(?:to\s+)?(?:speak|talk)\s+(?:to|with)\s+(?:someone|a\s+person)"
    r")\b",
    re.IGNORECASE,
)

_GENERAL_FRUSTRATION_RE = re.compile(
    r"\b(?:"
    r"this\s+is\s+(?:ridiculous|useless|annoying|frustrating|terrible|awful|pointless|a\s+waste)|"
    r"(?:waste\s+of\s+(?:my\s+)?time|wasting\s+my\s+time)|"
    r"you(?:'?re|\s+are)\s+(?:useless|terrible|awful|no\s+help|unhelpful|not\s+helpful)|"
    r"this\s+(?:doesn'?t|isn'?t)\s+(?:work|help|make\s+sense)|"
    r"(?:i'?m\s+)?(?:getting\s+)?(?:very\s+)?(?:frustrated|annoyed|fed\s+up)|"
    r"can'?t\s+(?:believe|understand)\s+(?:this|you|how)"
    r")\b",
    re.IGNORECASE,
)

_ABUSIVE_RE = re.compile(
    r"\b(?:fuck|shit|asshole|idiot|moron)\b",
    re.IGNORECASE,
)
# Note: "stupid bot", "useless bot", "damn" are handled by _DIRECTED_INSULT_RE
# and _GENERAL_FRUSTRATION_RE respectively. Keeping _ABUSIVE_RE narrow prevents
# double-classification that sends conflicting severity signals (warn vs HIGH).


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

_CATEGORY_SEVERITY: dict[ComplaintCategory, ComplaintSeverity] = {
    ComplaintCategory.ABUSIVE_OR_THREATENING: ComplaintSeverity.HIGH,
    ComplaintCategory.PRIVACY_COMPLAINT: ComplaintSeverity.HIGH,
    ComplaintCategory.HUMAN_HANDOFF_REQUEST: ComplaintSeverity.MEDIUM,
    ComplaintCategory.REPEATED_QUESTION: ComplaintSeverity.MEDIUM,
    ComplaintCategory.NOT_ANSWERING: ComplaintSeverity.MEDIUM,
    ComplaintCategory.WRONG_SERVICE: ComplaintSeverity.MEDIUM,
    ComplaintCategory.PRICE_OBJECTION: ComplaintSeverity.LOW,
    ComplaintCategory.GENERAL_FRUSTRATION: ComplaintSeverity.LOW,
}

# ---------------------------------------------------------------------------
# Recovery config per category
# ---------------------------------------------------------------------------

_CATEGORY_RECOVERY: dict[str, dict[str, object]] = {
    ComplaintCategory.PRIVACY_COMPLAINT: {
        "should_stop_sales_script": True,
        "should_apologize": True,
        "should_offer_handoff": False,
        "recovery_goal": "complaint_recovery",
        "forbidden_questions": ["name_and_email_or_phone", "email", "phone"],
    },
    ComplaintCategory.REPEATED_QUESTION: {
        "should_stop_sales_script": True,
        "should_apologize": True,
        "should_offer_handoff": False,
        "recovery_goal": "complaint_recovery",
        "forbidden_questions": [],
    },
    ComplaintCategory.NOT_ANSWERING: {
        "should_stop_sales_script": True,
        "should_apologize": False,
        "should_offer_handoff": False,
        "recovery_goal": "complaint_recovery",
        "forbidden_questions": [],
    },
    ComplaintCategory.WRONG_SERVICE: {
        "should_stop_sales_script": False,
        "should_apologize": False,
        "should_offer_handoff": False,
        "recovery_goal": "service_correction_recovery",
        "forbidden_questions": [],
    },
    ComplaintCategory.PRICE_OBJECTION: {
        "should_stop_sales_script": False,
        "should_apologize": False,
        "should_offer_handoff": True,
        "recovery_goal": "price_objection_response",
        "forbidden_questions": [],
    },
    ComplaintCategory.HUMAN_HANDOFF_REQUEST: {
        "should_stop_sales_script": True,
        "should_apologize": False,
        "should_offer_handoff": True,
        "recovery_goal": "human_handoff_offer",
        "forbidden_questions": [],
    },
    ComplaintCategory.GENERAL_FRUSTRATION: {
        "should_stop_sales_script": False,
        "should_apologize": True,
        "should_offer_handoff": False,
        "recovery_goal": "complaint_recovery",
        "forbidden_questions": [],
    },
    ComplaintCategory.ABUSIVE_OR_THREATENING: {
        "should_stop_sales_script": True,
        "should_apologize": False,
        "should_offer_handoff": False,
        "recovery_goal": "safety_boundary",
        "forbidden_questions": [],
    },
}


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class ComplaintClassifier:
    """Detects complaint signals and returns a structured classification.

    Priority order (first match wins — most critical categories first):
      1. abusive_or_threatening
      2. privacy_complaint
      3. human_handoff_request
      4. repeated_question
      5. not_answering
      6. wrong_service
      7. price_objection
      8. general_frustration
    """

    _ORDERED_CHECKS: list[tuple[ComplaintCategory, re.Pattern[str]]] = [
        (ComplaintCategory.ABUSIVE_OR_THREATENING, _ABUSIVE_RE),
        (ComplaintCategory.PRIVACY_COMPLAINT, _PRIVACY_RE),
        (ComplaintCategory.HUMAN_HANDOFF_REQUEST, _HUMAN_HANDOFF_RE),
        (ComplaintCategory.REPEATED_QUESTION, _REPEATED_QUESTION_RE),
        (ComplaintCategory.NOT_ANSWERING, _NOT_ANSWERING_RE),
        (ComplaintCategory.WRONG_SERVICE, _WRONG_SERVICE_RE),
        (ComplaintCategory.PRICE_OBJECTION, _PRICE_OBJECTION_RE),
        (ComplaintCategory.GENERAL_FRUSTRATION, _GENERAL_FRUSTRATION_RE),
    ]

    def classify(self, text: str) -> ComplaintClassification:
        audit: list[str] = []

        for category, pattern in self._ORDERED_CHECKS:
            if pattern.search(text):
                audit.append(f"signal:{category.value}")
                config = _CATEGORY_RECOVERY[category]
                severity = _CATEGORY_SEVERITY[category]
                return ComplaintClassification(
                    detected=True,
                    category=category,
                    severity=severity,
                    should_stop_sales_script=bool(config["should_stop_sales_script"]),
                    should_apologize=bool(config["should_apologize"]),
                    should_offer_handoff=bool(config["should_offer_handoff"]),
                    recovery_goal=str(config["recovery_goal"]),
                    forbidden_questions=(
                        list(config["forbidden_questions"])
                        if isinstance(config["forbidden_questions"], list)
                        else []
                    ),
                    audit=audit,
                )

        audit.append("signal:none")
        return ComplaintClassification(detected=False, audit=audit)
