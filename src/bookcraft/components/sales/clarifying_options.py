"""ClarifyingOptionsBuilder.

Produces structured option guidance for clarifying questions.
Engines compute. Claude writes final customer-facing prose.
No hardcoded final text is returned — all output is internal guidance.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Option item model
# ---------------------------------------------------------------------------


class ClarifyingOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    hint: str = ""


# ---------------------------------------------------------------------------
# Options registry
# ---------------------------------------------------------------------------


def _opt(key: str, label: str, hint: str = "") -> ClarifyingOption:
    return ClarifyingOption(key=key, label=label, hint=hint)


_OPTIONS_REGISTRY: dict[str, list[ClarifyingOption]] = {
    "service_options": [
        _opt("ghostwriting", "Writing / ghostwriting", "write the book"),
        _opt("editing_proofreading", "Editing / proofreading", "polish existing manuscript"),
        _opt("cover_design_illustration", "Cover design", "book cover and illustrations"),
        _opt("interior_formatting", "Formatting", "interior layout for print or ebook"),
        _opt("publishing_distribution", "Publishing / distribution", "getting the book to market"),
        _opt("marketing_promotion", "Marketing", "book launch and promotion"),
        _opt(
            "fine_art_monograph",
            "Fine-Art & Monograph Publishing",
            "coffee-table, art, or photography books",
        ),
        _opt(
            "catalog_transition",
            "Catalog Transition",
            "moving your backlist when a publisher closes",
        ),
        _opt(
            "publishing_partnership",
            "Publishing Partnership",
            "ongoing hybrid or full-service publishing",
        ),
        _opt(
            "author_brand_platform",
            "Author Brand & Platform",
            "newsletter, website, audience building",
        ),
        _opt(
            "translation_foreign_rights",
            "Translation & Foreign Rights",
            "publishing in other languages or markets",
        ),
        _opt(
            "special_collector_editions",
            "Special & Collector Editions",
            "signed, limited, deluxe, or boxed sets",
        ),
        _opt("not_sure", "Not sure yet", "need guidance on which service"),
    ],
    "genre_options": [
        _opt("fiction", "Fiction", "novel, short story, narrative fiction"),
        _opt("memoir", "Memoir / personal story", "real-life personal narrative"),
        _opt("business_self_help", "Business / self-help", "professional or instructional book"),
        _opt("childrens_book", "Children's book", "book for young readers"),
        _opt("not_sure", "Not sure yet", "still figuring out the category"),
    ],
    "manuscript_stage_options": [
        _opt("idea", "Just an idea", "no writing done yet"),
        _opt("rough_notes", "Rough notes", "scattered ideas or journal entries"),
        _opt("outline", "Outline", "structured plan but not written"),
        _opt("partial_draft", "Partial draft", "some chapters written"),
        _opt("full_draft", "Full draft", "complete but needs work"),
        _opt("completed", "Completed manuscript", "ready for editing or publishing"),
    ],
    "how_can_we_help": [
        _opt("writing", "Get a book written", "ghostwriting from idea to manuscript"),
        _opt("editing", "Edit an existing manuscript", "editing, proofreading, polishing"),
        _opt("design_format", "Cover design or formatting", "visual and layout work"),
        _opt("publish_distribute", "Publish or distribute", "getting book to market"),
        _opt("marketing", "Market the book", "launch and promotion"),
        _opt("not_sure", "Not sure — need advice", "guidance on where to start"),
    ],
    "consultation_interest": [
        _opt("yes_schedule", "Yes, schedule a call", "connect with a specialist"),
        _opt("more_info_first", "Tell me more first", "more information before deciding"),
        _opt("not_now", "Not right now", "continue exploring independently"),
    ],
    "preferred_call_time": [
        _opt("morning", "Morning", "before noon"),
        _opt("afternoon", "Afternoon", "noon to 5 PM"),
        _opt("evening", "Evening", "after 5 PM"),
        _opt("flexible", "Flexible / anytime", "any time works"),
    ],
    "name_and_email_or_phone": [
        _opt("provide_contact", "Share name and email", "name + email to connect"),
        _opt("provide_phone", "Share name and phone", "name + phone number"),
        _opt("later", "Maybe later", "continue exploring first"),
    ],
}


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class ClarifyingOptionsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_key: str
    options: list[ClarifyingOption] = Field(default_factory=list)
    found: bool = False


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class ClarifyingOptionsBuilder:
    """Returns structured option lists for clarifying question keys.

    Pass the result to Claude as response plan guidance — not as final prose.
    """

    def build(self, question_key: str) -> ClarifyingOptionsResult:
        options = _OPTIONS_REGISTRY.get(question_key, [])
        return ClarifyingOptionsResult(
            question_key=question_key,
            options=list(options),
            found=bool(options),
        )

    @staticmethod
    def all_keys() -> list[str]:
        return list(_OPTIONS_REGISTRY.keys())
