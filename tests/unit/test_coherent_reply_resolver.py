"""Tests for CoherentReplyResolver."""

from __future__ import annotations

import pytest

from bookcraft.components.context.coherent_reply import CoherentReplyResolver
from bookcraft.domain.state import ThreadState


@pytest.fixture
def resolver() -> CoherentReplyResolver:
    return CoherentReplyResolver()


@pytest.fixture
def empty_state() -> ThreadState:
    return ThreadState()


def test_word_count_reply_maps_to_pending_word_count(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="about 60000 words",
        state=empty_state,
        last_assistant_question="What's the rough word count or page count?",
    )
    assert len(resolutions) == 1
    assert resolutions[0].resolved is True
    assert resolutions[0].slot_path == "project.word_count"
    assert resolutions[0].value == 60000


def test_word_count_k_suffix_maps_correctly(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="60k",
        state=empty_state,
        last_assistant_question="How many words is the manuscript?",
    )
    assert len(resolutions) == 1
    assert resolutions[0].slot_path == "project.word_count"
    assert resolutions[0].value == 60000


def test_page_count_reply_maps_to_pending_page_count(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="about 250 pages",
        state=empty_state,
        last_assistant_question="What's the rough word count or page count?",
    )
    assert len(resolutions) == 1
    assert resolutions[0].slot_path == "project.page_count"
    assert resolutions[0].value == 250


def test_rough_notes_reply_maps_to_manuscript_status(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="just rough notes at the moment",
        state=empty_state,
        last_assistant_question="What stage is the manuscript in?",
    )
    assert len(resolutions) == 1
    assert resolutions[0].slot_path == "project.manuscript_status"
    assert resolutions[0].value == "rough_notes"


def test_outline_reply_maps_to_manuscript_status(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="I have an outline",
        state=empty_state,
        last_assistant_question="Have you started writing or do you have a draft?",
    )
    matching = [r for r in resolutions if r.slot_path == "project.manuscript_status"]
    assert matching
    assert matching[0].value == "outline"


def test_contact_reply_maps_to_pending_contact(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="Sarah, sarah@example.com",
        state=empty_state,
        last_assistant_question="What's the best name and email or phone number?",
    )
    email_res = [r for r in resolutions if r.slot_path == "personal.email"]
    assert email_res
    assert email_res[0].value == "sarah@example.com"


def test_call_time_reply_maps_to_pending_consultation_time(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="tomorrow afternoon works for me",
        state=empty_state,
        last_assistant_question="When would you prefer a call? What time works?",
    )
    time_res = [r for r in resolutions if r.slot_path == "preferred_call_time"]
    assert time_res
    assert "tomorrow" in str(time_res[0].value).lower()


def test_uncertain_genre_reply_does_not_confirm_genre(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="not sure, maybe memoir or business",
        state=empty_state,
        last_assistant_question="What genre or type of book is this?",
    )
    genre_res = [r for r in resolutions if r.slot_path == "project.genre"]
    # If a resolution is returned for genre, the value must be None (unconfirmed).
    for res in genre_res:
        assert res.value is None, "Uncertain genre must not produce a confirmed genre value"


def test_no_resolutions_for_unrelated_text(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="I'm just browsing for now",
        state=empty_state,
    )
    confirmed_genre = [r for r in resolutions if r.slot_path == "project.genre" and r.value]
    assert not confirmed_genre


def test_bare_number_resolves_word_count_when_pending(
    resolver: CoherentReplyResolver, empty_state: ThreadState
) -> None:
    resolutions = resolver.resolve(
        text="80000",
        state=empty_state,
        next_question="word_or_page_count",
    )
    wc = [r for r in resolutions if r.slot_path == "project.word_count"]
    assert wc
    assert wc[0].value == 80000
