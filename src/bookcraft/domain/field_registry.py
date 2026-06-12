"""Unified field/schema registry for BookCraft extractable facts.

Provides a single source of truth for all fields that can be:
- Extracted from user messages (deterministic or LLM)
- Stored in ThreadState
- Surfaced in context packs
- Used as forbidden-reask guards in TRG

Usage:
    from bookcraft.domain.field_registry import FIELD_REGISTRY, FieldDef

    field = FIELD_REGISTRY["project.genre"]
    print(field.display_name)  # "Genre"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal


FieldType = Literal["str", "int", "float", "bool", "enum", "list", "range"]
ExtractionSource = Literal["deterministic", "llm", "both", "none"]


@dataclass(frozen=True)
class FieldDef:
    """Definition of a single extractable field."""

    path: str                          # Dot-path in ThreadState, e.g. "project.genre"
    display_name: str                  # Human-readable name, e.g. "Genre"
    field_type: FieldType = "str"      # Data type
    extraction_source: ExtractionSource = "both"  # Where this field gets populated
    required_for_quote: bool = False   # Must be known before pricing
    pii: bool = False                  # Contains PII — handle with care in logs
    reask_phrases: list[str] = field(default_factory=list)  # Forbidden reask phrases when known
    validator: Callable[[Any], bool] | None = field(default=None, compare=False, hash=False)
    description: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FIELD_REGISTRY: dict[str, FieldDef] = {}


def _register(*defs: FieldDef) -> None:
    for d in defs:
        FIELD_REGISTRY[d.path] = d


_register(
    # Contact / Personal
    FieldDef(
        path="contact.name",
        display_name="Name",
        field_type="str",
        extraction_source="llm",
        pii=True,
        reask_phrases=["your name", "what's your name", "may i have your name"],
    ),
    FieldDef(
        path="contact.email",
        display_name="Email Address",
        field_type="str",
        extraction_source="llm",
        pii=True,
        reask_phrases=["email", "email address", "your email"],
    ),
    FieldDef(
        path="contact.phone",
        display_name="Phone Number",
        field_type="str",
        extraction_source="llm",
        pii=True,
        reask_phrases=["phone", "phone number", "contact number"],
    ),
    # Project
    FieldDef(
        path="project.genre",
        display_name="Genre",
        field_type="str",
        extraction_source="both",
        reask_phrases=["genre", "what genre"],
    ),
    FieldDef(
        path="project.word_count",
        display_name="Word Count",
        field_type="int",
        extraction_source="both",
        required_for_quote=True,
        reask_phrases=["word count", "how many words", "length of your manuscript"],
    ),
    FieldDef(
        path="project.page_count",
        display_name="Page Count",
        field_type="int",
        extraction_source="both",
        required_for_quote=True,
        reask_phrases=["page count", "how many pages"],
    ),
    FieldDef(
        path="project.manuscript_status",
        display_name="Manuscript Status",
        field_type="enum",
        extraction_source="both",
        reask_phrases=["manuscript_stage", "draft status", "starting from scratch"],
    ),
    FieldDef(
        path="project.title",
        display_name="Book Title",
        field_type="str",
        extraction_source="llm",
        reask_phrases=["title", "book title", "name of your book"],
    ),
    FieldDef(
        path="project.formats",
        display_name="Book Formats",
        field_type="list",
        extraction_source="both",
        reask_phrases=["format", "book format", "paperback or ebook"],
    ),
    FieldDef(
        path="project.platforms",
        display_name="Publishing Platforms",
        field_type="list",
        extraction_source="both",
        reask_phrases=["platform", "publishing platform", "where will you publish"],
    ),
    # Service / Commercial
    FieldDef(
        path="service.timeline",
        display_name="Timeline",
        field_type="str",
        extraction_source="both",
        required_for_quote=True,
        reask_phrases=["timeline", "when do you need", "deadline"],
    ),
    FieldDef(
        path="service.budget",
        display_name="Budget",
        field_type="range",
        extraction_source="llm",
        reask_phrases=["budget", "how much are you looking to spend"],
    ),
)


def get_required_for_quote() -> list[FieldDef]:
    """Return all fields required before a pricing quote can be generated."""
    return [f for f in FIELD_REGISTRY.values() if f.required_for_quote]


def get_forbidden_reasks(known_paths: list[str]) -> list[str]:
    """Return all forbidden reask phrases for a set of known fact paths."""
    phrases: list[str] = []
    for path in known_paths:
        field_def = FIELD_REGISTRY.get(path)
        if field_def:
            phrases.extend(field_def.reask_phrases)
    return phrases
