from __future__ import annotations

from bookcraft.components.sales.narrative_sharing import NarrativeSharingDetector

_detector = NarrativeSharingDetector()


def test_third_person_life_story_is_narrative() -> None:
    # chat 6688: the author recounting her husband's history.
    r = _detector.detect("He was in prison for 35 years")
    assert r.is_narrative is True
    assert r.confidence >= 0.75


def test_relationship_and_project_framing_is_narrative() -> None:
    r = _detector.detect("My husband has a very great story to tell and it is time we do this")
    assert r.is_narrative is True


def test_emotional_detail_is_narrative() -> None:
    r = _detector.detect(
        "Yes he is free and thriving not without obstacles of being institutionalised"
    )
    assert r.is_narrative is True


def test_question_is_not_narrative() -> None:
    assert _detector.detect("How much do you charge?").is_narrative is False


def test_transactional_message_is_not_narrative() -> None:
    # Even with a narrative-ish subject, a pricing/scheduling ask defers to normal flow.
    assert _detector.detect("What does it cost for my husband's life story").is_narrative is False
    assert _detector.detect("Can we schedule a consultation for the story").is_narrative is False


def test_short_fragments_are_not_narrative() -> None:
    assert _detector.detect("Bio").is_narrative is False
    assert _detector.detect("Life story").is_narrative is False
    assert _detector.detect("Thank you again").is_narrative is False


def test_bare_name_is_not_narrative() -> None:
    assert _detector.detect("My name is Deborah Houston").is_narrative is False


def test_empty_is_not_narrative() -> None:
    assert _detector.detect("").is_narrative is False
    assert _detector.detect("   ").is_narrative is False
