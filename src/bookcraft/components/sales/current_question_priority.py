"""CurrentQuestionPriorityDetector.

Detects when the latest user turn contains a direct buying or informational
question that must be answered *before* contact capture or slot collection.

Engines compute. Claude writes final customer-facing text.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Compiled patterns — one per question type
# ---------------------------------------------------------------------------

_PRICING_RE = re.compile(
    r"\b(?:how\s+much|what(?:'s|\s+is)\s+(?:the\s+)?(?:cost|price|rate|charge)|"
    r"what\s+do\s+you\s+charge|pricing|cost\s+(?:of|for)|"
    r"how\s+much\s+(?:does|would|will|do)|quoted?\s+(?:for|me)|"
    r"price\s+(?:for|of)|rates?\s+(?:for|of))\b",
    re.IGNORECASE,
)

_ROUGH_ESTIMATE_RE = re.compile(
    r"\b(?:rough\s+(?:range|estimate|idea|ballpark|price|cost)|"
    r"ballpark\s+(?:figure|price|cost|estimate)?|approximate\s+(?:cost|price)?|"
    r"general\s+(?:idea|range)|before\s+(?:giving|sharing|providing)"
    r"(?:\s+(?:contact|my|any|personal))?\s*(?:info(?:rmation)?|details?)?|"
    r"(?:price|cost)\s+range|range\s+(?:of\s+)?(?:prices?|costs?))\b",
    re.IGNORECASE,
)

_TIMELINE_RE = re.compile(
    r"\b(?:how\s+long|turnaround|timeline|time\s+(?:frame|to\s+complete|it\s+takes?)|"
    r"when\s+(?:will|can|could)\s+(?:it|you)|how\s+fast|delivery\s+time|"
    r"how\s+quickly|expected\s+(?:timeline|turnaround|time)|"
    r"how\s+many\s+(?:days|weeks|months))\b",
    re.IGNORECASE,
)

_SAMPLES_RE = re.compile(
    r"\b(?:samples?|"
    # "example" must not be part of an email domain — add negative lookbehind for '@'.
    r"(?<!@)examples?|portfolio|previous\s+work|show\s+me|"
    r"can\s+(?:i|you)\s+see|evidence\s+of|past\s+(?:work|projects?)|"
    r"work\s+(?:you'?ve|you\s+have)\s+done|show\s+(?:me|some)\s+(?:work|example|sample))\b",
    re.IGNORECASE,
)

_DISTRIBUTION_RE = re.compile(
    r"\b(?:distribut(?:ion|e|ing|ed|or|ors?)|publish(?:ing|ed)\s+on|"
    r"amazon\s+(?:kdp|publishing)|kdp|ingramspark|ingram\s+spark|"
    r"barnes\s+(?:and|&)\s+noble|kobo|apple\s+books|wide\s+distribution|"
    r"where\s+(?:will|can|would)\s+(?:it\s+be\s+sold|the\s+book|readers?\s+find)|"
    r"retail(?:er|ers|ing)?|bookstore|brick\s+and\s+mortar|how\s+(?:do|does|will)\s+"
    r"(?:distribution|publishing)|your\s+distribution)\b",
    re.IGNORECASE,
)

_CHRISTIAN_RE = re.compile(
    r"\b(?:christian\s+(?:publish(?:er|ing|ed)|book|market|fiction|nonfiction|content|author)|"
    r"faith[\s-]based\s+(?:publish(?:ing|er)|book|manuscript)?|"
    r"religious\s+(?:publish(?:er|ing)|book|market)|"
    r"christian\s+(?:book)?store|biblical|faith\s+community|"
    r"work(?:ed)?\s+with\s+christian)\b",
    re.IGNORECASE,
)

_FIVERR_RE = re.compile(
    r"\b(?:fiverr|upwork|freelancer\.com|guru\.com|"
    r"freelance\s+(?:writer|editor|designer)|"
    r"why\s+not\s+(?:fiverr|freelance|upwork)|"
    r"compared?\s+to\s+(?:fiverr|freelance)|"
    r"(?:versus|vs\.?)\s+(?:fiverr|freelance|upwork)|"
    r"cheaper\s+(?:on|than|at)\s+(?:fiverr|upwork)|"
    r"difference\s+(?:between|from)\s+(?:fiverr|freelance))\b",
    re.IGNORECASE,
)

_FREE_SAMPLE_RE = re.compile(
    r"\b(?:free\s+(?:sample|edit(?:ing)?|chapter|consultation|trial|review)|"
    r"sample\s+(?:before|first|then)|test\s+(?:edit|write|piece)|"
    r"trial\s+(?:edit|chapter|project))\b",
    re.IGNORECASE,
)

_PROCESS_RE = re.compile(
    r"\b(?:how\s+(?:do(?:es)?\s+it\s+work|does\s+the\s+process|will\s+(?:it|we)\s+work)|"
    r"what(?:'s|\s+is)\s+(?:the\s+)?(?:process|workflow|involved|next\s+steps?)|"
    r"walk\s+me\s+through|tell\s+me\s+about\s+(?:the\s+)?(?:process|how\s+(?:it|you)\s+work)|"
    r"what\s+happens\s+(?:after|next|then)|how\s+(?:it|things?)\s+work|"
    r"steps?\s+(?:involved|to\s+get\s+started)|how\s+do\s+we\s+(?:start|begin|proceed))\b",
    re.IGNORECASE,
)

_SERVICE_ADVICE_RE = re.compile(
    r"\b(?:which\s+service|what\s+service|what\s+do\s+(?:i|you)\s+need|"
    r"what\s+would\s+you\s+recommend|advice\s+on\s+(?:which|what)|"
    r"best\s+(?:service|option|approach|choice)\s+for|"
    r"should\s+i\s+(?:get|choose|go\s+with|use|start\s+with)|"
    r"what\s+(?:services?\s+do\s+you|can\s+you\s+help\s+with))\b",
    re.IGNORECASE,
)

_GUARANTEE_RE = re.compile(
    r"\b(?:guarantee(?:d|s)?|promise(?:d|s)?|ensure\s+(?:quality|success|results?)|"
    r"bestseller|number\s+one|refund\s+policy|money[\s-]back|"
    r"satisfaction\s+guaranteed|100\s*(?:%|percent)\s+(?:guaranteed|satisfied)|"
    r"what\s+(?:results?|outcomes?)\s+(?:do|can)\s+(?:you|i)\s+(?:guarantee|expect))\b",
    re.IGNORECASE,
)

_CONTACT_REFUSAL_RE = re.compile(
    r"\b(?:don'?t\s+(?:want\s+to\s+(?:give|share|provide)|like\s+to\s+share)|"
    r"not\s+(?:ready\s+to\s+(?:share|give|provide)|comfortable\s+(?:giving|sharing))|"
    r"before\s+(?:giving|sharing|providing)\s+(?:my\s+)?(?:contact|info(?:rmation)?|"
    r"details?|name|email|number)|"
    r"prefer\s+(?:to\s+stay\s+anonymous|not\s+to\s+share|not\s+sharing)|"
    r"can\s+(?:we|you|i)\s+(?:first|start|begin)\s+without\s+(?:my|the\s+)?(?:contact|info|name)|"
    r"without\s+(?:giving|sharing)\s+(?:my\s+)?(?:contact|info|name|email|number)|"
    r"keep\s+(?:my\s+)?(?:info|details?|contact)\s+(?:private|to\s+myself|anonymous)|"
    r"i\s+(?:don'?t|won'?t)\s+(?:give|share|provide)\s+(?:my\s+)?(?:contact|info|email|name|number))\b",
    re.IGNORECASE,
)

_TOPIC_CORRECTION_RE = re.compile(
    r"\b(?:i\s+was\s+(?:asking|talking|referring)\s+(?:about|to)|"
    r"back\s+to\s+my\s+(?:question|point|concern|original\s+question)|"
    r"that'?s\s+not\s+what\s+i\s+(?:asked|meant|said|was\s+asking)|"
    r"my\s+(?:question|concern|point)\s+was|"
    r"you\s+(?:didn'?t\s+answer|haven'?t\s+answered|ignored|missed)|"
    r"can\s+you\s+(?:answer|address)\s+(?:my|the)\s+(?:question|concern|point)|"
    r"i\s+(?:meant|was\s+asking\s+about)|"
    r"let\s+me\s+(?:rephrase|clarify|be\s+more\s+specific)|"
    r"to\s+be\s+(?:clear|specific|direct)|"
    r"(?:what|that)\s+(?:i\s+(?:actually|really)\s+)?(?:asked|wanted\s+to\s+know)\s+was)\b",
    re.IGNORECASE,
)

# Ordered list of (question_type, pattern) tuples.
# Topic correction is checked first because it often wraps other question types.
_PRIORITY_CHECKS: list[tuple[str, re.Pattern[str]]] = [
    ("contact_refusal", _CONTACT_REFUSAL_RE),
    ("topic_correction", _TOPIC_CORRECTION_RE),
    ("distribution", _DISTRIBUTION_RE),
    ("christian_publishing", _CHRISTIAN_RE),
    ("fiverr_comparison", _FIVERR_RE),
    ("free_sample", _FREE_SAMPLE_RE),
    ("guarantee_or_sales_claim", _GUARANTEE_RE),
    ("rough_estimate", _ROUGH_ESTIMATE_RE),
    ("pricing", _PRICING_RE),
    ("timeline", _TIMELINE_RE),
    ("samples", _SAMPLES_RE),
    ("process", _PROCESS_RE),
    ("service_advice", _SERVICE_ADVICE_RE),
]

# Question types that should suppress the old sales path (e.g. restart-from-distribution).
_SUPPRESS_OLD_PATH_TYPES: frozenset[str] = frozenset(
    {"topic_correction", "contact_refusal", "distribution", "christian_publishing"}
)

# Question types that must be answered before asking for contact.
_ANSWER_BEFORE_CAPTURE_TYPES: frozenset[str] = frozenset(
    {
        "pricing",
        "rough_estimate",
        "timeline",
        "samples",
        "distribution",
        "christian_publishing",
        "fiverr_comparison",
        "free_sample",
        "process",
        "service_advice",
        "guarantee_or_sales_claim",
        "contact_refusal",
        "topic_correction",
    }
)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class CurrentQuestionPriorityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_priority: bool = False
    question_type: str | None = None
    should_answer_before_capture: bool = False
    suppress_old_sales_path: bool = False
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class CurrentQuestionPriorityDetector:
    """
    Detects direct buying/informational questions that must be answered
    before the bot asks for contact details or resumes old slot loops.

    Engines compute. Claude writes.
    """

    def detect(self, text: str) -> CurrentQuestionPriorityResult:
        audit: list[str] = []

        for question_type, pattern in _PRIORITY_CHECKS:
            if pattern.search(text):
                answer_first = question_type in _ANSWER_BEFORE_CAPTURE_TYPES
                suppress = question_type in _SUPPRESS_OLD_PATH_TYPES
                audit.append(f"matched:{question_type}")
                return CurrentQuestionPriorityResult(
                    has_priority=True,
                    question_type=question_type,
                    should_answer_before_capture=answer_first,
                    suppress_old_sales_path=suppress,
                    audit=audit,
                )

        audit.append("no_priority_question")
        return CurrentQuestionPriorityResult(audit=audit)
