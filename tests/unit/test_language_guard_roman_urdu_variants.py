"""Roman-Urdu detection robustness regression.

Live miss: "kia haal he kese ho?" was answered conversationally in English instead
of triggering the English-only redirect, because only "kia" was a known marker
(spelling variants "haal"/"kese" were absent) and a single marker in a 5-token
message did not meet the old dominance rule. Fix: cover common transliteration
variants AND treat a single Urdu marker with ZERO English function words as
non-English, length-independently.
"""

from __future__ import annotations

import pytest

from bookcraft.components.language_guard.guard import LanguageGuard

_guard = LanguageGuard(enabled=True)


@pytest.mark.parametrize(
    "message",
    [
        "kia haal he kese ho?",
        "kaise ho bhai",
        "kese ho",
        "shukriya",
        "mujhe madad chahiye",
        "aap kaisay hain",
        "thek hai",
    ],
)
def test_roman_urdu_variants_redirect(message: str) -> None:
    d = _guard.detect(message)
    assert not d.is_english, f"{message!r} should redirect, got {d.language}/{d.source}"
    assert d.redirect_message


@pytest.mark.parametrize(
    "message",
    [
        "I want editing",
        "can you help me publish my book",
        "yes please book it",
        "Consultation?",
        "I'm in EST timezone",
        "What is the price for a cover?",
        "no thanks",
        "hello",
        "Tomorrow at 3pm works",
    ],
)
def test_plain_english_not_redirected(message: str) -> None:
    d = _guard.detect(message)
    assert d.is_english, f"{message!r} should stay English, got {d.language}/{d.source}"


@pytest.mark.parametrize(
    "message",
    [
        "I need help with editing mera book",
        "Can you edit my book? mera naam Ali hai",
        "What is the price aur kitna time lagega",
    ],
)
def test_mixed_language_proceeds_not_redirected(message: str) -> None:
    """Mixed English+foreign: answered by Claude (English part + polite ask), not a
    hard redirect. Guard flags is_mixed and lets the turn proceed."""
    d = _guard.detect(message)
    assert d.is_mixed is True
    assert d.is_english is True  # proceeds through the pipeline
    assert d.redirect_message is None


@pytest.mark.parametrize(
    "message",
    [
        "Price kya hai editing ka?",   # Urdu sentence with English loanwords -> redirect
        "mujhe editing chahiye",
    ],
)
def test_urdu_with_loanwords_still_redirects(message: str) -> None:
    d = _guard.detect(message)
    assert d.is_mixed is False
    assert d.is_english is False
    assert d.source == "roman_urdu"
