from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Lexicons — longer / more-specific phrases first to ensure correct matching
# ---------------------------------------------------------------------------

# (phrase, service_key)
_SERVICE_LEXICON: list[tuple[str, str]] = [
    ("writing from scratch", "ghostwriting"),
    ("write from scratch", "ghostwriting"),
    ("ghost writer", "ghostwriting"),
    ("ghostwriter", "ghostwriting"),
    ("ghostwriting", "ghostwriting"),
    ("writer", "ghostwriting"),
    ("interior formatting", "interior_formatting"),
    ("cover design", "cover_design_illustration"),
    ("book cover", "cover_design_illustration"),
    ("cover illustration", "cover_design_illustration"),
    ("audio book", "audiobook_production"),
    ("audiobook", "audiobook_production"),
    ("author website", "author_website"),
    ("video trailer", "video_trailer"),
    ("proofreading", "editing_proofreading"),
    ("editing", "editing_proofreading"),
    ("editor", "editing_proofreading"),
    ("formatting", "interior_formatting"),
    ("illustration", "cover_design_illustration"),
    ("cover", "cover_design_illustration"),
    ("layout", "interior_formatting"),
    ("distribution", "publishing_distribution"),
    ("publishing", "publishing_distribution"),
    ("kdp", "publishing_distribution"),
    ("marketing", "marketing_promotion"),
    ("promotion", "marketing_promotion"),
    ("ads", "marketing_promotion"),
    ("website", "author_website"),
    ("trailer", "video_trailer"),
]

# (phrase, action_key)
_ACTION_LEXICON: list[tuple[str, str]] = [
    ("service agreement", "generate_agreement"),
    ("non-disclosure agreement", "generate_nda"),
    ("non disclosure agreement", "generate_nda"),
    ("price quote", "price_quote"),
    ("pricing estimate", "price_quote"),
    ("portfolio lookup", "portfolio_lookup"),
    ("non-disclosure", "generate_nda"),
    ("non disclosure", "generate_nda"),
    ("samples", "portfolio_lookup"),
    ("portfolio", "portfolio_lookup"),
    ("examples", "portfolio_lookup"),
    ("consultation", "schedule_consultation"),
    ("appointment", "schedule_consultation"),
    ("meeting", "schedule_consultation"),
    ("agreement", "generate_agreement"),
    ("contract", "generate_agreement"),
    ("pricing", "price_quote"),
    ("estimate", "price_quote"),
    ("quote", "price_quote"),
    ("price", "price_quote"),
    ("cost", "price_quote"),
    ("nda", "generate_nda"),
]

# (phrase, document_key)
_DOCUMENT_LEXICON: list[tuple[str, str]] = [
    ("service agreement", "agreement"),
    ("non-disclosure", "nda"),
    ("non disclosure", "nda"),
    ("agreement", "agreement"),
    ("contract", "agreement"),
    ("nda", "nda"),
]

# (phrase, slot_key)
_SLOT_LEXICON: list[tuple[str, str]] = [
    ("manuscript stage", "manuscript_stage"),
    ("draft status", "manuscript_stage"),
    ("word count", "word_count"),
    ("page count", "page_count"),
    ("cover style", "cover_style"),
    ("visual direction", "cover_style"),
    ("genre", "genre"),
    ("category", "genre"),
    ("deadline", "deadline"),
]

# (phrase, project_target_value)
_PROJECT_PHRASES: list[tuple[str, str]] = [
    ("my other book", "other_project"),
    ("my other one", "other_project"),
    ("my other", "other_project"),
    ("other book", "other_project"),
    ("another book", "other_project"),
    ("second book", "other_project"),
    ("previous book", "other_project"),
    ("different book", "other_project"),
    ("this project", "active_project"),
    ("current book", "active_project"),
    ("this book", "active_project"),
    ("this one", "active_project"),
]

# (phrase, deadline_target_value)
_TIMING_LEXICON: list[tuple[str, str]] = [
    ("right now", "immediate"),
    ("not yet", "immediate"),
    ("now", "immediate"),
    ("yet", "immediate"),
    ("today", "immediate"),
]

# ---------------------------------------------------------------------------
# Contrast patterns — capture (neg, aff) named groups
# ---------------------------------------------------------------------------

_CONTRAST_PATTERNS: list[re.Pattern[str]] = [
    # "don't / do not need X, (but/just) (I) (do) need Y"
    re.compile(
        r"(?:don'?t|do\s+not|doesn'?t|does\s+not|didn'?t|did\s+not)\s+"
        r"(?:need|want|use|get|have|require)?\s*"
        r"(?P<neg>[^,;.!?\n]{2,50}?)"
        r"\s*,\s*(?:but\s+)?(?:just\s+)?(?:i\s+)?(?:do\s+)?"
        r"(?:need|want|use|get|have|require|prefer|would\s+like)\s+"
        r"(?P<aff>[^,;.!?\n]{2,50})",
        re.IGNORECASE,
    ),
    # "no X, (only/just) Y"
    re.compile(
        r"(?<!\w)no\s+(?P<neg>[^,;.!?\n]{2,50}?)\s*,\s*(?:only\s+|just\s+)"
        r"(?P<aff>[^,;.!?\n]{2,50})",
        re.IGNORECASE,
    ),
    # "not X, (but/only/just) Y"
    re.compile(
        r"(?<!\w)not\s+(?P<neg>[^,;.!?\n]{2,50}?)\s*,\s*(?:but\s+)?(?:only\s+|just\s+)?"
        r"(?P<aff>[^,;.!?\n]{2,50})",
        re.IGNORECASE,
    ),
    # "don't send/trigger/generate X (yet), just (show) (me) Y"
    re.compile(
        r"(?:don'?t|do\s+not)\s+(?:send|trigger|generate|produce|show)\s+"
        r"(?P<neg>[^,;.!?\n]{2,50}?)"
        r"(?:\s+yet\b)?\s*,\s*(?:just\s+)?(?:show\s+)?(?:me\s+)?"
        r"(?P<aff>[^,;.!?\n]{2,50})",
        re.IGNORECASE,
    ),
]

# Simple single-negation (no contrast) — only used when no contrast match found
_SIMPLE_NEG_RE = re.compile(
    r"(?:don'?t|do\s+not|doesn'?t|does\s+not|didn'?t|did\s+not|"
    r"(?:^|\s)no\s+|(?:^|\s)not\s+|without\s+)"
    r"(?:(?:need|want|use|get|have|require|a|an|the)\s+)?"
    r"(?P<neg>[^,;.!?\n]{2,60}?)(?=\s*[,;.!?]|\s*$)",
    re.IGNORECASE,
)

# Project-switch shorthand
_PROJECT_SWITCH_RE = re.compile(
    r"(?:not\s+this|don'?t\s+use\s+this)\s+(?:book|project|manuscript)\s*"
    r"[,;]\s*(?:my\s+)?(?:other|another|different)\s+(?:one|book|project|manuscript)\b",
    re.IGNORECASE,
)

# Timing/deadline negation
_TIMING_NEG_RE = re.compile(
    r"\bnot\s+now\b|\bnot\s+yet\b|\bdon'?t\s+(?:send|show|do)\s+(?:\w+\s+)?yet\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class NegationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal[
        "service", "query", "tool_action", "document", "slot", "project", "deadline", "pricing"
    ]
    target: str
    polarity: Literal["negated", "affirmed", "replacement"]
    replacement: str | None = None
    span_text: str
    confidence: float


class NegationTargetResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[NegationTarget] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class NegationTargetResolver:
    """Binds each negation cue to the exact target it negates, and surfaces
    any replacement/affirmed target from the same contrast structure.

    Uses reusable pattern groups and lexicons — not one-off per-case checks.
    """

    def resolve(
        self,
        *,
        text: str,
        services: list[str] | None = None,
        negation_spans: list[Any] | None = None,
        counterfactual_spans: list[Any] | None = None,
    ) -> NegationTargetResolution:
        del services, negation_spans  # used via lexicon-based classification instead

        targets: list[NegationTarget] = []
        audit: list[str] = []
        contrast_found = False

        # --- Pass 1: explicit contrast structures ---
        for pattern in _CONTRAST_PATTERNS:
            for m in pattern.finditer(text):
                neg_text = m.group("neg").strip()
                aff_text = m.group("aff").strip()

                neg_classified = _classify_segment(neg_text)
                aff_classified = _classify_segment(aff_text)

                if not neg_classified and not aff_classified:
                    continue

                contrast_found = True
                neg_types: dict[str, str] = {}

                for tt, tv in neg_classified:
                    targets.append(
                        NegationTarget(
                            target_type=tt,
                            target=tv,
                            polarity="negated",
                            span_text=neg_text,
                            confidence=0.92,
                        )
                    )
                    neg_types[tt] = tv
                    audit.append(f"contrast:negated:{tt}:{tv}")

                # Check whether the affirmed segment is counterfactual.
                aff_start = m.start("aff")
                aff_end = m.end("aff")
                aff_is_cf = bool(
                    counterfactual_spans
                    and _overlaps_spans(aff_start, aff_end, counterfactual_spans)
                )

                if not aff_is_cf:
                    for tt, tv in aff_classified:
                        polarity: Literal["negated", "affirmed", "replacement"] = (
                            "replacement" if tt in neg_types else "affirmed"
                        )
                        targets.append(
                            NegationTarget(
                                target_type=tt,
                                target=tv,
                                polarity=polarity,
                                replacement=neg_types.get(tt),
                                span_text=aff_text,
                                confidence=0.92,
                            )
                        )
                        audit.append(f"contrast:{polarity}:{tt}:{tv}")
                else:
                    audit.append(f"contrast:aff_counterfactual_skipped:{aff_text[:20]}")

        # --- Pass 2: project-switch shorthand ---
        if _PROJECT_SWITCH_RE.search(text):
            targets.append(
                NegationTarget(
                    target_type="project",
                    target="active_project",
                    polarity="negated",
                    span_text="this book/project",
                    confidence=0.90,
                )
            )
            targets.append(
                NegationTarget(
                    target_type="project",
                    target="other_project",
                    polarity="replacement",
                    replacement="active_project",
                    span_text="other/another book",
                    confidence=0.90,
                )
            )
            audit.append("project_switch_detected")
            contrast_found = True

        # --- Pass 3: timing / deadline negation ---
        timing_m = _TIMING_NEG_RE.search(text)
        if timing_m:
            targets.append(
                NegationTarget(
                    target_type="deadline",
                    target="immediate",
                    polarity="negated",
                    span_text=timing_m.group(0),
                    confidence=0.85,
                )
            )
            audit.append("timing_negation_detected")

        # --- Pass 4: single negation fallback (only when no contrast found) ---
        if not contrast_found:
            for m in _SIMPLE_NEG_RE.finditer(text):
                neg_text = m.group("neg").strip()
                # Skip if negated segment is counterfactual
                if counterfactual_spans and _overlaps_spans(
                    m.start(), m.end(), counterfactual_spans
                ):
                    audit.append(f"simple_neg:counterfactual_skipped:{neg_text[:20]}")
                    continue
                classified = _classify_segment(neg_text)
                for tt, tv in classified:
                    targets.append(
                        NegationTarget(
                            target_type=tt,
                            target=tv,
                            polarity="negated",
                            span_text=neg_text,
                            confidence=0.75,
                        )
                    )
                    audit.append(f"simple_negated:{tt}:{tv}")

        return NegationTargetResolution(targets=_dedupe(targets), audit=audit)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_segment(text: str) -> list[tuple[str, str]]:
    """Map a text segment to (target_type, target_value) tuples using lexicons."""
    tl = text.casefold().strip()
    results: list[tuple[str, str]] = []

    # Action lexicon (checked first for specificity — NDA/agreement are actions AND documents).
    for phrase, action in _ACTION_LEXICON:
        if phrase in tl:
            results.append(("tool_action", action))
            break

    # Document lexicon — may overlap with action.
    for phrase, doc in _DOCUMENT_LEXICON:
        if phrase in tl:
            results.append(("document", doc))
            break

    # Service lexicon — stop at first match to avoid duplicates.
    for phrase, service in _SERVICE_LEXICON:
        if phrase in tl:
            results.append(("service", service))
            break

    # Project phrases.
    for phrase, proj_val in _PROJECT_PHRASES:
        if phrase in tl:
            results.append(("project", proj_val))
            break

    # Timing / deadline.
    for phrase, timing_val in _TIMING_LEXICON:
        if phrase in tl:
            results.append(("deadline", timing_val))
            break

    # Slot lexicon.
    for phrase, slot in _SLOT_LEXICON:
        if phrase in tl:
            results.append(("slot", slot))
            break

    return results


def _overlaps_spans(start: int, end: int, spans: list[Any]) -> bool:
    for span in spans:
        s = span.start if hasattr(span, "start") else span.get("start", 0)
        e = span.end if hasattr(span, "end") else span.get("end", 0)
        if start < e and end > s:
            return True
    return False


def _dedupe(targets: list[NegationTarget]) -> list[NegationTarget]:
    seen: set[tuple[str, str, str]] = set()
    out: list[NegationTarget] = []
    for t in targets:
        key = (t.target_type, t.target, t.polarity)
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out
