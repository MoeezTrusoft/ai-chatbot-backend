"""Regression for chat 6685 — the language guard flip-flopped on Roman Urdu.

Support is ENGLISH-ONLY. Roman Urdu (Latin-script Urdu/Hindi) is now detected
length-independently and redirected consistently, so the bot never again answers a
customer in Urdu on one turn and refuses them on the next. English replies — short
or long — must stay English (no false positives).
"""

import pytest

from bookcraft.components.language_guard.guard import LanguageGuard


@pytest.fixture
def guard() -> LanguageGuard:
    return LanguageGuard(enabled=True)


# Messages from the actual chat 6685 transcript that previously got ANSWERED (in
# Urdu) because they were short or contained an incidental English hint word.
_ROMAN_URDU_MESSAGES = [
    "kia kehrahay hoo",
    "kia pagal pana hai?",
    "naam kia hai tumhara",
    "acha chalo achi baat hai",
    "apka daftar kahan hai?",
    "ye kitaab ek ghoray k baray main hai",
    "tum insaan hoo ya masnoi zahanat?",
    "phr wohi fazul bakwas",
    "abay nai bhai kisi ko bata denan apni harkatain",
    "urdu main jawab tumhara baap deraha thaaa pehle",
    "choro angerezi urdu main hee baat karlo",
    "acha",
    "likhli hai",
]


@pytest.mark.parametrize("message", _ROMAN_URDU_MESSAGES)
def test_roman_urdu_is_consistently_redirected(guard: LanguageGuard, message: str):
    decision = guard.detect(message)
    assert decision.is_english is False, f"{message!r} leaked through as English"
    assert decision.redirect_message is not None
    assert decision.language == "ur"


# English replies — short and long — must NOT be misclassified as Urdu.
_ENGLISH_MESSAGES = [
    "ok",
    "yes please",
    "sure sounds good",
    "no thanks",
    "hi",
    "I'm in EST timezone",
    "Yes need to publish it",
    "are you suffering from split personality disorder?",
    "What format would you like, ebook or paperback?",
    "Tomorrow afternoon works for me",
    "2+2?",
]


@pytest.mark.parametrize("message", _ENGLISH_MESSAGES)
def test_english_stays_english(guard: LanguageGuard, message: str):
    decision = guard.detect(message)
    assert decision.is_english is True, f"{message!r} was wrongly flagged non-English"
    assert decision.redirect_message is None


def test_single_urdu_word_dominates_short_message():
    assert guard_detect_is_urdu("acha")
    assert guard_detect_is_urdu("theek hai")


def test_incidental_english_hint_no_longer_rescues_urdu():
    """'baray hee dheet ... hoo' contains no ENGLISH_HINTS; the old ascii fast-path
    used to rescue Urdu sentences that happened to include a hint word like 'or'."""
    g = LanguageGuard(enabled=True)
    # 'or' (English hint) embedded in an otherwise Roman-Urdu sentence must not flip
    # the whole message to English.
    decision = g.detect("baray hee dheet or bagairat hoo agar insaan hoo tou")
    assert decision.is_english is False


def guard_detect_is_urdu(text: str) -> bool:
    return LanguageGuard(enabled=True).detect(text).is_english is False
