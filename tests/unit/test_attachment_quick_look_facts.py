"""Attachment 'quick look' facts (feature: human first-impression on upload).

When the upload service supplies pre-extracted metadata (page/word count, a short
opening excerpt, image dimensions) on a ChatAttachment, the response planner must
surface those as acknowledge_facts so the LLM can narrate a human first impression
("a ~134-page draft that reads like a memoir") WITHOUT ever reading the file itself.
The backend still performs no content analysis — it only relays what Node extracted.
"""

from __future__ import annotations

from bookcraft.components.attachments.intake import ChatAttachment
from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.planner import ResponsePlanner
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState

_planner = ResponsePlanner()


def _intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=ServiceCategory.EDITING_PROOFREADING,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )


def _plan_with(attachments: list[ChatAttachment]):
    pack = ContextPack(
        attachments_received=attachments,
        assessment_type="editorial_assessment",
        specialist_role="senior editorial specialist",
    )
    return _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)


def test_manuscript_enrichment_surfaces_count_and_excerpt_facts() -> None:
    att = ChatAttachment(
        filename="my-novel.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        category="manuscript",
        page_count=134,
        word_count=40200,
        excerpt="It was the winter my mother finally stopped speaking to the sea.",
    )
    facts = _plan_with([att]).acknowledge_facts
    assert any("attachment_filename" in f and "my-novel.docx" in f for f in facts)
    assert any("attachment_page_count: 134" in f for f in facts)
    assert any("attachment_word_count: 40200" in f for f in facts)
    assert any("attachment_excerpt:" in f and "winter my mother" in f for f in facts)


def test_cover_image_surfaces_dimensions_fact() -> None:
    att = ChatAttachment(
        filename="cover-final.png",
        mime_type="image/png",
        category="cover_design",
        image_width=1600,
        image_height=2400,
    )
    facts = _plan_with([att]).acknowledge_facts
    assert any("attachment_image_dimensions: 1600x2400" in f for f in facts)


def test_excerpt_fact_is_length_capped() -> None:
    att = ChatAttachment(
        filename="huge.docx",
        category="manuscript",
        excerpt="x" * 5000,
    )
    facts = _plan_with([att]).acknowledge_facts
    excerpt_facts = [f for f in facts if f.startswith("attachment_excerpt:")]
    assert excerpt_facts
    # "attachment_excerpt: " prefix + at most 500 chars of excerpt.
    assert len(excerpt_facts[0]) <= len("attachment_excerpt: ") + 500


def test_no_enrichment_still_acknowledges_by_name_only() -> None:
    att = ChatAttachment(filename="mystery.pdf", category="manuscript")
    facts = _plan_with([att]).acknowledge_facts
    assert any("attachment_filename" in f and "mystery.pdf" in f for f in facts)
    assert not any("attachment_page_count" in f for f in facts)
    assert not any("attachment_excerpt" in f for f in facts)
