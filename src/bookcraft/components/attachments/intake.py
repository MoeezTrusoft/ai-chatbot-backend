"""Attachment metadata intake processor.

Classifies file attachments by category and maps them to the appropriate
BookCraft assessment type and specialist role.

This backend performs NO content analysis of its own — it never opens or parses
file bytes. It may, however, receive *pre-extracted* light metadata from the upload
service (the Node app, which already holds the bytes): page/word count and a short
opening excerpt. These let the bot give a human "quick look" acknowledgement
("a ~134-page draft that reads like a family memoir") without ever claiming to have
read the manuscript. Only metadata crosses this boundary — never the file itself.
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
    # Pre-extracted "quick look" metadata supplied by the upload service (Node). The
    # backend never computes these — it only narrates them. All optional; absent for
    # files the upload service could not (or chose not to) peek at.
    page_count: int | None = None
    word_count: int | None = None
    # A short opening excerpt (plain text, already length-capped by the upload service).
    # Used only so the bot can characterize the material in its OWN words — never quoted.
    excerpt: str | None = None
    # Image dimensions (e.g. for cover-design uploads).
    image_width: int | None = None
    image_height: int | None = None


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

# A pasted link to an uploaded file (e.g. the chat media host, or a direct document/
# image/audio link) must be treated as an attachment so the bot acknowledges it instead
# of replying "I don't see an attachment". The path may contain spaces, so we scan up to
# the first known file extension on the same line. (BUG-6040: customer pasted
# "https://server.trusoft.pk/media/assets/Chapter 2_….docx" and it was never registered.)
_DOC_EXTS = (
    "docx", "doc", "pdf", "rtf", "txt", "odt", "epub", "pages",
    "png", "jpg", "jpeg", "gif", "webp", "tif", "tiff",
    "mp3", "wav", "m4a", "aac", "ogg", "flac",
)
_MEDIA_URL_RE = re.compile(
    r"https?://\S[^\n]{0,300}?\.(?:" + "|".join(_DOC_EXTS) + r")\b",
    re.IGNORECASE,
)
_EXT_TO_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pdf": "application/pdf",
    "rtf": "application/rtf",
    "txt": "text/plain",
    "odt": "application/vnd.oasis.opendocument.text",
    "epub": "application/epub+zip",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
}


def _attachments_from_message(message: str) -> list[ChatAttachment]:
    """Synthesize attachments from media URLs pasted in the message text."""
    import urllib.parse as _urlparse

    out: list[ChatAttachment] = []
    seen: set[str] = set()
    for match in _MEDIA_URL_RE.finditer(message or ""):
        url = match.group(0).strip()
        if url in seen:
            continue
        seen.add(url)
        tail = url.rsplit("/", 1)[-1]
        filename = _urlparse.unquote(tail).strip() or "attachment"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        out.append(
            ChatAttachment(filename=filename, mime_type=_EXT_TO_MIME.get(ext), storage_key=url)
        )
    return out


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
    "fine_art_monograph": {
        "manuscript": "manuscript_assessment",
        "brief": "manuscript_assessment",
    },
    "catalog_transition": {
        "manuscript": "manuscript_assessment",
        "brief": "manuscript_assessment",
    },
    "publishing_partnership": {
        "manuscript": "manuscript_assessment",
        "brief": "manuscript_assessment",
    },
    "author_brand_platform": {"brief": "manuscript_assessment"},
    "translation_foreign_rights": {
        "manuscript": "manuscript_assessment",
        "brief": "manuscript_assessment",
    },
    "special_collector_editions": {
        "manuscript": "manuscript_assessment",
        "brief": "manuscript_assessment",
    },
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
        # File CONTENTS are never analysed — but a media URL pasted in the message
        # text counts as an attachment the customer shared (the widget/Node may not
        # have forwarded the upload metadata).
        url_attachments = _attachments_from_message(message)
        combined: list[Any] = list(attachments or []) + list(url_attachments)

        if not combined:
            return AttachmentIntakeResult(
                audit=["no_attachments"],
            )

        classified: list[ChatAttachment] = []
        audit: list[str] = []
        if url_attachments:
            audit.append(f"media_url_detected:{len(url_attachments)}")

        for raw in combined:
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
