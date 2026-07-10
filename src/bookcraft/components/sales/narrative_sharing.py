"""NarrativeSharingDetector.

Detects when the author is *telling their story* — sharing personal history,
events, or the emotional substance of the book — rather than asking for pricing,
scheduling, or any other transactional next step.

Many memoir/biography/ghostwriting customers open up at length before they are
ready to talk logistics (chat 6688: an author described her husband's 35-year
prison sentence and release, and the bot kept nudging pricing/consultation
instead of listening). When this signal fires on an established project thread,
the response planner switches to a warm "listen and reflect" goal instead of the
default discovery/scoping push.

Engines compute. Claude writes final customer-facing text.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

# Minimum word count before a declarative statement counts as narrative
# elaboration. Short fragments ("Bio", "Life story") stay with the normal
# clarify path — they are not yet a story.
_MIN_NARRATIVE_WORDS = 5
_MIN_DECLARATIVE_WORDS = 10

# Relational subjects and framing that signal the author is recounting a life.
_NARRATIVE_CUE_RE = re.compile(
    r"\b(?:"
    # Relationship subjects ("my husband", "his mother", "our family").
    r"(?:my|his|her|our|their)\s+(?:husband|wife|father|mother|dad|mom|son|"
    r"daughter|brother|sister|grandfather|grandmother|grandpa|grandma|family|"
    r"life|childhood|story|journey|past)|"
    # Explicit book/story framing.
    r"(?:it'?s|this\s+is|the\s+(?:book|story|memoir))\s+(?:about|based\s+on)|"
    r"true\s+story|based\s+on\s+(?:a\s+)?true|life\s+story|his\s+story|her\s+story|"
    # Life-event vocabulary that memoirs are built from.
    r"prison|sentenced?|parole|incarcerat(?:ed|ion)|released|convicted|"
    r"born|died|passed\s+away|war|army|military|veteran|survived|survivor|"
    r"diagnos(?:ed|is)|cancer|addiction|recovery|sober|immigrant|refugee|"
    r"escaped|orphan|adopted|homeless|abuse|abused|divorce|overcame|struggl(?:e|ed)|"
    r"grew\s+up|raised|institutional[a-z]*|juvenile\s+offender"
    r")\b",
    re.IGNORECASE,
)

# Third-person past-tense narration ("He was in prison", "she had spent years").
_THIRD_PERSON_PAST_RE = re.compile(
    r"\b(?:he|she|they)\s+(?:was|were|had|has|got|went|spent|served|grew|"
    r"became|lived|died|survived|escaped|left|came|ended\s+up)\b",
    re.IGNORECASE,
)

# Transactional / logistics keywords — when present the author is asking to move
# forward, NOT (only) telling their story, so we defer to the normal flow.
_TRANSACTIONAL_RE = re.compile(
    r"\b(?:price|pricing|cost|charge|quote|fee|rate|budget|"
    r"schedule|consultation|appointment|call\s+time|book\s+(?:a|me)|"
    r"my\s+(?:email|phone|number)\s+is|contact\s+me|sample|portfolio|"
    r"nda|agreement|contract|discount|refund|invoice)\b",
    re.IGNORECASE,
)


class NarrativeSharingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_narrative: bool = False
    confidence: float = 0.0
    audit: list[str] = Field(default_factory=list)


class NarrativeSharingDetector:
    """Detects story/narrative sharing turns so the planner can stay in listening
    mode instead of pushing scoping or consultation.

    Deliberately conservative (high precision): a false negative just keeps the
    normal flow, but a false positive would suppress a legitimate next step, so we
    only fire on clear narrative cues or a substantial declarative statement.
    """

    def detect(self, text: str) -> NarrativeSharingResult:
        audit: list[str] = []
        stripped = (text or "").strip()

        if not stripped:
            audit.append("empty")
            return NarrativeSharingResult(audit=audit)

        # A question is a request for information — let question handling run.
        if "?" in stripped:
            audit.append("has_question")
            return NarrativeSharingResult(audit=audit)

        # Transactional turns (pricing, scheduling, contact) are not story-sharing.
        if _TRANSACTIONAL_RE.search(stripped):
            audit.append("transactional_keyword")
            return NarrativeSharingResult(audit=audit)

        word_count = len(re.findall(r"[A-Za-z']+", stripped))
        if word_count < _MIN_NARRATIVE_WORDS:
            audit.append(f"too_short:{word_count}")
            return NarrativeSharingResult(audit=audit)

        has_cue = bool(_NARRATIVE_CUE_RE.search(stripped))
        has_narration = bool(_THIRD_PERSON_PAST_RE.search(stripped))

        if has_cue or has_narration:
            confidence = 0.9 if (has_cue and has_narration) else 0.75
            audit.append(f"narrative_cue:cue={has_cue}:narration={has_narration}")
            return NarrativeSharingResult(
                is_narrative=True, confidence=confidence, audit=audit
            )

        # Softer path: a substantial declarative statement with no transactional
        # ask reads as the author elaborating on their project.
        if word_count >= _MIN_DECLARATIVE_WORDS:
            audit.append(f"declarative_elaboration:{word_count}")
            return NarrativeSharingResult(
                is_narrative=True, confidence=0.6, audit=audit
            )

        audit.append("no_narrative_signal")
        return NarrativeSharingResult(audit=audit)
