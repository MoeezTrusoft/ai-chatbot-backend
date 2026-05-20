"""Attachment metadata intake processor.

Classifies file attachments by category and maps them to the appropriate
BookCraft assessment type and specialist role.

Content analysis is NEVER performed. Only filename/MIME metadata is used.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_AttachmentCategory = Literal[
    "manuscript",
    "cover_design",
    "brief",
    "sample_reference",
    "outline",
    "notes",
    "audio",
    "other",
]


class ChatAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    mime_type: str | None = None
    storage_key: str | None = None
    category: _AttachmentCategory | None = None
    size_bytes: int | None = None


class AttachmentIntakeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachments: list[ChatAttachment] = Field(default_factory=list)
    detected_categories: list[str] = Field(default_factory=list)
    assessment_type: str | None = None
    specialist_role: str | None = None
    content_analysis_allowed: bool = False
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Category inference rules (filename patterns)
# ---------------------------------------------------------------------------

_CATEGORY_PATTERNS: list[tuple[re.Pattern[str], _AttachmentCategory]] = [
    # audio (check before notes to avoid "audio_notes" being classified as notes)
    (re.compile(r"\b(?:mp3|wav|m4a|aac|ogg|flac|voice|audio)\b", re.IGNORECASE), "audio"),
    # outline (check before manuscript since "book_outline" should be outline not manuscript)
    (re.compile(r"\b(?:outline|plot|synopsis|plan)\b", re.IGNORECASE), "outline"),
    # notes (check before manuscript since "diary_notes" should be notes)
    (re.compile(r"\b(?:notes|note|journal|diary|memo)\b", re.IGNORECASE), "notes"),
    # cover design (check before manuscript since "cover" is more specific)
    (
        re.compile(r"\b(?:cover|design|jacket|front.cover|artwork|illustration)\b", re.IGNORECASE),
        "cover_design",
    ),
    # sample reference
    (
        re.compile(
            r"\b(?:sample|reference|inspiration|moodboard|mood.board|example)\b",
            re.IGNORECASE,
        ),
        "sample_reference",
    ),
    # brief
    (re.compile(r"\b(?:brief|requirements|instruction|spec)\b", re.IGNORECASE), "brief"),
    # manuscript (broad terms — last among content types)
    (
        re.compile(r"\b(?:manuscript|ms|draft|book|chapter|novel|story)\b", re.IGNORECASE),
        "manuscript",
    ),
]

# File extension → audio category
_AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "m4a", "aac", "ogg", "flac", "wma", "opus"})


def _infer_category(filename: str) -> _AttachmentCategory:
    """Infer attachment category from filename without reading file contents."""
    # Strip extension and replace word separators for reliable matching.
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    # Normalise separators so word-boundary matching works on underscored names.
    normalised = stem.replace("_", " ").replace("-", " ")

    if ext in _AUDIO_EXTENSIONS:
        return "audio"

    for pattern, category in _CATEGORY_PATTERNS:
        if pattern.search(normalised):
            return category
        # Also search against the raw filename in case the pattern expects original chars.
        if pattern.search(filename):
            return category

    return "other"


# ---------------------------------------------------------------------------
# Assessment type mapping
# ---------------------------------------------------------------------------

_MANUSCRIPT_EARLY_STAGES = frozenset(
    {"idea", "rough_notes", "journal_entries", "voice_memo", "outline", "idea_only"}
)

_ASSESSMENT_MAP: dict[str, dict[str, str]] = {
    "editing_proofreading": {"manuscript": "editorial_assessment"},
    "ghostwriting": {
        "manuscript": "manuscript_development_assessment",
        "outline": "manuscript_development_assessment",
        "notes": "manuscript_development_assessment",
        "audio": "manuscript_development_assessment",
    },
    "cover_design_illustration": {
        "cover_design": "cover_design_assessment",
        "sample_reference": "cover_design_assessment",
        "brief": "cover_design_assessment",
    },
    "publishing_distribution": {"manuscript": "publishing_readiness_assessment"},
    "marketing_promotion": {},  # any category → marketing
    "audiobook_production": {"audio": "audiobook_assessment", "manuscript": "audiobook_assessment"},
    "author_website": {},
    "video_trailer": {},
}

_SERVICE_DEFAULT_ASSESSMENT: dict[str, str] = {
    "editing_proofreading": "editorial_assessment",
    "ghostwriting": "manuscript_development_assessment",
    "cover_design_illustration": "cover_design_assessment",
    "publishing_distribution": "publishing_readiness_assessment",
    "marketing_promotion": "marketing_assessment",
    "audiobook_production": "audiobook_assessment",
    "author_website": "website_assessment",
    "video_trailer": "video_trailer_assessment",
}

_SPECIALIST_MAP: dict[str, str] = {
    "editorial_assessment": "senior editorial specialist",
    "manuscript_assessment": "senior manuscript specialist",
    "manuscript_development_assessment": "senior manuscript specialist",
    "cover_design_assessment": "senior cover design specialist",
    "publishing_readiness_assessment": "senior publishing specialist",
    "marketing_assessment": "senior marketing specialist",
    "audiobook_assessment": "senior audiobook specialist",
    "website_assessment": "senior website specialist",
    "video_trailer_assessment": "senior video trailer specialist",
    "general_project_assessment": "senior project specialist",
}


def _determine_assessment(
    categories: Sequence[str],
    active_service: str | None,
    manuscript_status: str | None,
) -> tuple[str | None, str | None]:
    """Return (assessment_type, specialist_role)."""
    service = active_service or ""

    # Service + category specific lookup.
    if service in _ASSESSMENT_MAP:
        service_map = _ASSESSMENT_MAP[service]
        for cat in categories:
            if cat in service_map:
                assessment = service_map[cat]
                return assessment, _SPECIALIST_MAP.get(assessment)

    # Ghostwriting + early manuscript stage.
    if service == "ghostwriting" and manuscript_status in _MANUSCRIPT_EARLY_STAGES:
        assessment = "manuscript_development_assessment"
        return assessment, _SPECIALIST_MAP.get(assessment)

    # Service default.
    if service in _SERVICE_DEFAULT_ASSESSMENT:
        assessment = _SERVICE_DEFAULT_ASSESSMENT[service]
        return assessment, _SPECIALIST_MAP.get(assessment)

    # Category fallback.
    if "manuscript" in categories:
        return "manuscript_assessment", _SPECIALIST_MAP.get("manuscript_assessment")
    if "cover_design" in categories:
        return "cover_design_assessment", _SPECIALIST_MAP.get("cover_design_assessment")
    if "audio" in categories:
        return "audiobook_assessment", _SPECIALIST_MAP.get("audiobook_assessment")
    if any(c in categories for c in ("outline", "notes")):
        return "manuscript_assessment", _SPECIALIST_MAP.get("manuscript_assessment")

    return "general_project_assessment", _SPECIALIST_MAP.get("general_project_assessment")


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------


class AttachmentIntakeProcessor:
    """Classifies attachment metadata and routes to the correct assessment path.

    Content analysis is NEVER performed — only filename/MIME metadata is used.
    """

    def process(
        self,
        *,
        attachments: list[Any] | None,
        message: str,
        active_service: str | None = None,
        manuscript_status: str | None = None,
    ) -> AttachmentIntakeResult:
        del message  # not used for content analysis

        if not attachments:
            return AttachmentIntakeResult(
                audit=["no_attachments"],
            )

        classified: list[ChatAttachment] = []
        audit: list[str] = []

        for raw in attachments:
            if isinstance(raw, ChatAttachment):
                att = raw
            elif isinstance(raw, dict):
                att = ChatAttachment.model_validate(raw)
            else:
                audit.append(f"skipped_non_dict_attachment:{type(raw).__name__}")
                continue

            # Infer category if not already set.
            if att.category is None:
                att = att.model_copy(update={"category": _infer_category(att.filename)})

            classified.append(att)
            audit.append(f"classified:{att.filename}:{att.category}")

        detected_categories = list(dict.fromkeys(a.category for a in classified if a.category))

        assessment_type, specialist_role = _determine_assessment(
            detected_categories, active_service, manuscript_status
        )

        audit.append(f"assessment_type:{assessment_type}")
        audit.append(f"specialist_role:{specialist_role}")
        audit.append("content_analysis_allowed:False")

        return AttachmentIntakeResult(
            attachments=classified,
            detected_categories=detected_categories,
            assessment_type=assessment_type,
            specialist_role=specialist_role,
            content_analysis_allowed=False,
            audit=audit,
        )
