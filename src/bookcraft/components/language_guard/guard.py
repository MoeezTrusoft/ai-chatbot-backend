import re
import time
from dataclasses import dataclass

import structlog
from prometheus_client import Counter, Histogram

from bookcraft.components.language_guard.models import LanguageDecision
from bookcraft.components.language_guard.pii_masking import is_predominantly_pii, mask_pii

LANGUAGE_DETECTION_SECONDS = Histogram(
    "language_detection_seconds",
    "Latency for language detection.",
    ["source"],
)
LANGUAGE_DETECTION_RESULTS = Counter(
    "language_detection_results_total",
    "Language detection results.",
    ["language"],
)
NON_ENGLISH_REDIRECTS = Counter(
    "non_english_redirects_total",
    "Non-English redirects issued.",
    ["language"],
)

ENGLISH_HINTS = {
    "a",
    "about",
    "and",
    "book",
    "cover",
    "editing",
    "for",
    "ghostwriting",
    "hello",
    "help",
    "hi",
    "i",
    "marketing",
    "my",
    "need",
    "price",
    "pricing",
    "publish",
    "the",
    "want",
    "website",
    "writing",
}


@dataclass(slots=True)
class LanguageGuard:
    enabled: bool = True

    def detect(self, text: str, cached_language: str | None = None) -> LanguageDecision:
        started = time.perf_counter()
        if not self.enabled:
            return self._record("en", True, 1.0, "disabled", started)

        stripped = text.strip()
        if len(stripped) < 12:
            language = cached_language or "en"
            return self._record(language, language == "en", 0.9, "short_message", started)

        # PII/contact bypass: messages that are predominantly contact info must not be
        # rejected as non-English (names, emails, phone numbers are language-neutral).
        if is_predominantly_pii(stripped):
            return self._record("en", True, 0.95, "pii_bypass", started)

        # Run detection on PII-masked text to prevent name/email bias.
        pii_result = mask_pii(stripped)
        detection_text = pii_result.masked_text if pii_result.has_pii else stripped

        ascii_ratio = self._ascii_ratio(detection_text)
        english_hint_count = len(
            set(re.findall(r"[a-zA-Z']+", detection_text.lower())) & ENGLISH_HINTS
        )
        if ascii_ratio >= 0.95 and english_hint_count > 0:
            return self._record("en", True, 0.95, "ascii_fast_path", started)

        try:
            return self._detect_with_lingua(detection_text, started)
        except Exception as exc:
            structlog.get_logger(__name__).warning("language_detection_failed", error=str(exc))
            return self._record("en", True, 0.5, "failure_default", started)

    def _detect_with_lingua(self, text: str, started: float) -> LanguageDecision:
        from lingua import Language, LanguageDetectorBuilder

        detector = LanguageDetectorBuilder.from_languages(
            Language.ENGLISH,
            Language.SPANISH,
            Language.FRENCH,
            Language.GERMAN,
            Language.PORTUGUESE,
            Language.ITALIAN,
            Language.CHINESE,
            Language.JAPANESE,
            Language.ARABIC,
            Language.HINDI,
            Language.RUSSIAN,
        ).build()
        language = detector.detect_language_of(text)
        if language is None:
            return self._record("en", True, 0.5, "lingua_low_confidence", started)
        iso = language.iso_code_639_1.name.lower()
        is_english = iso == "en"
        return self._record(iso, is_english, 0.8, "lingua", started)

    def _record(
        self,
        language: str,
        is_english: bool,
        confidence: float,
        source: str,
        started: float,
    ) -> LanguageDecision:
        LANGUAGE_DETECTION_SECONDS.labels(source=source).observe(time.perf_counter() - started)
        LANGUAGE_DETECTION_RESULTS.labels(language=language).inc()
        redirect = None
        if not is_english:
            NON_ENGLISH_REDIRECTS.labels(language=language).inc()
            redirect = (
                "BookCraft support is currently available in English. "
                "Please send your message in English so I can help."
            )
        return LanguageDecision(
            language=language,
            is_english=is_english,
            confidence=confidence,
            source=source,
            redirect_message=redirect,
        )

    @staticmethod
    def _ascii_ratio(text: str) -> float:
        if not text:
            return 1.0
        ascii_chars = sum(1 for char in text if ord(char) < 128)
        return ascii_chars / len(text)
