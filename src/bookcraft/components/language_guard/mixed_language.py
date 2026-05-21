"""Mixed-language partial handler — preserves English intent when non-English is mixed in."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

_ENGLISH_CONTENT_RE = re.compile(
    r"\b(?:book|writing|editing|publishing|ghostwriting|cover|design|format|"
    r"marketing|story|novel|manuscript|memoir|fiction|need|want|help|service|"
    r"price|cost|quote|sample|review|chapter|word|page|deadline|author|"
    r"distribution|illustration|audiobook|trailer|website)\b",
    flags=re.IGNORECASE,
)

_NON_ASCII_BLOCK_RE = re.compile(r"[^\x00-\x7F]+")


class MixedLanguageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_mixed: bool = False
    english_intent_clear: bool = False
    ignored_segments: list[dict[str, str]] = Field(default_factory=list)
    english_portion: str = ""
    audit: list[str] = Field(default_factory=list)


def detect_mixed_language(text: str, detected_language: str) -> MixedLanguageResult:
    """
    Detect and handle a message containing clear English mixed with non-English text.

    When English intent is clear, preserve it and mark non-English segments as ignored.
    Does NOT hallucinate translations. Does NOT reject the entire message when English
    intent can be determined.
    """
    audit: list[str] = []

    if detected_language == "en":
        audit.append("language:english_no_mixing")
        return MixedLanguageResult(
            english_portion=text,
            english_intent_clear=True,
            audit=audit,
        )

    english_matches = list(_ENGLISH_CONTENT_RE.finditer(text))
    if not english_matches:
        audit.append("no_english_content_found")
        return MixedLanguageResult(audit=audit)

    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    audit.append(f"ascii_ratio:{ascii_ratio:.2f}")

    if ascii_ratio < 0.25:
        audit.append("ascii_ratio_too_low")
        return MixedLanguageResult(audit=audit)

    # Split into ASCII and non-ASCII segments.
    non_english_segs: list[dict[str, str]] = []
    english_parts: list[str] = []

    segments = _NON_ASCII_BLOCK_RE.split(text)
    non_ascii_matches = _NON_ASCII_BLOCK_RE.findall(text)

    for i, segment in enumerate(segments):
        clean = segment.strip()
        if clean:
            english_parts.append(clean)
        if i < len(non_ascii_matches):
            non_english_segs.append({"text": non_ascii_matches[i].strip(), "position": str(i)})

    english_portion = " ".join(p for p in english_parts if p)

    if not english_portion:
        audit.append("no_english_portion_extracted")
        return MixedLanguageResult(
            is_mixed=bool(non_english_segs),
            ignored_segments=non_english_segs,
            audit=audit,
        )

    has_english_intent = bool(_ENGLISH_CONTENT_RE.search(english_portion))
    audit.append(f"english_intent:{has_english_intent}")
    audit.append(f"non_english_segments:{len(non_english_segs)}")

    return MixedLanguageResult(
        is_mixed=True,
        english_intent_clear=has_english_intent,
        ignored_segments=non_english_segs,
        english_portion=english_portion,
        audit=audit,
    )
