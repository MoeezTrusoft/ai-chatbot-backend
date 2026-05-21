"""Tests for PII masking and mixed-language detection."""

from __future__ import annotations

from bookcraft.components.language_guard.guard import LanguageGuard
from bookcraft.components.language_guard.mixed_language import detect_mixed_language
from bookcraft.components.language_guard.pii_masking import is_predominantly_pii, mask_pii

# ---------------------------------------------------------------------------
# PII masking unit tests
# ---------------------------------------------------------------------------


def test_email_only_is_predominantly_pii() -> None:
    assert is_predominantly_pii("sarah@example.com") is True


def test_name_and_email_is_predominantly_pii() -> None:
    assert is_predominantly_pii("Sarah, sarah@example.com") is True


def test_phone_only_is_predominantly_pii() -> None:
    assert is_predominantly_pii("+92 300 1234567") is True


def test_name_only_short_is_predominantly_pii() -> None:
    assert is_predominantly_pii("Maham Qureshi") is True


def test_long_english_text_is_not_predominantly_pii() -> None:
    assert (
        is_predominantly_pii(
            "I need ghostwriting for my memoir, please contact me at sarah@example.com"
        )
        is False
    )


def test_mask_pii_replaces_email() -> None:
    result = mask_pii("Send details to sarah@example.com please")
    assert "[EMAIL]" in result.masked_text
    assert "email" in result.pii_types
    assert result.has_pii is True


def test_mask_pii_replaces_phone() -> None:
    result = mask_pii("Call me at +92 300 1234567")
    assert "[PHONE]" in result.masked_text
    assert "phone" in result.pii_types


def test_mask_pii_replaces_name() -> None:
    result = mask_pii("My name is Sarah Johnson")
    assert result.has_pii is True


# ---------------------------------------------------------------------------
# Language guard — email/name bypass
# ---------------------------------------------------------------------------


def test_email_only_bypasses_language_detection() -> None:
    guard = LanguageGuard(enabled=True)
    decision = guard.detect("sarah@example.com")
    # Must be treated as English (PII bypass), not rejected as unknown language.
    assert decision.is_english is True
    assert decision.redirect_message is None


def test_name_and_email_bypass_language_detection() -> None:
    guard = LanguageGuard(enabled=True)
    decision = guard.detect("Maham Qureshi, maham@example.com")
    assert decision.is_english is True
    assert decision.redirect_message is None


def test_phone_bypasses_language_detection() -> None:
    guard = LanguageGuard(enabled=True)
    decision = guard.detect("+92 300 1234567")
    assert decision.is_english is True


# ---------------------------------------------------------------------------
# Mixed-language detection
# ---------------------------------------------------------------------------


def test_mixed_english_non_english_preserves_english_intent() -> None:
    result = detect_mixed_language(
        "I need editing for my book, meri file ready hai",
        detected_language="ur",
    )
    assert result.is_mixed is True
    assert result.english_intent_clear is True
    assert "editing" in result.english_portion or "book" in result.english_portion


def test_pure_english_is_not_mixed() -> None:
    result = detect_mixed_language("I need editing for my book.", detected_language="en")
    assert result.is_mixed is False
    assert result.english_intent_clear is True


def test_non_english_without_english_content_is_not_clear() -> None:
    result = detect_mixed_language("مجھے مدد چاہیے", detected_language="ur")
    assert result.english_intent_clear is False
