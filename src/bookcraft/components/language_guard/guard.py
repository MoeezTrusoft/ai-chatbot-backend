import re
import time
from dataclasses import dataclass

import structlog
from prometheus_client import Counter, Histogram

from bookcraft.components.language_guard.models import LanguageDecision
from bookcraft.components.language_guard.pii_masking import is_predominantly_pii, mask_pii

# ---------------------------------------------------------------------------
# Step 14: Roman Urdu / Hinglish lead-gen vocabulary
# ---------------------------------------------------------------------------

# Common Roman Urdu phrases that indicate a publishing/book service intent.
# Detected BEFORE the hard language redirect so these customers are not lost.
_ROMAN_URDU_INTENT_RE = re.compile(
    r"\b(?:kitab|book\s+publish|meri\s+book|meri\s+kitab|publish\s+karwani|"
    r"publish\s+karna|editing\s+chahiye|cover\s+design\s+chahiye|"
    r"ghostwriting\s+chahiye|price\s+kya\s+hai|kitna\s+(?:cost|price)|"
    r"consultation\s+chahiye|call\s+karein|call\s+karo|"
    r"writing\s+(?:karwani|karna)|"
    r"format(?:ting)?\s+chahiye|marketing\s+chahiye|website\s+chahiye)\b",
    re.IGNORECASE,
)

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
    # BookCraft service words
    "a", "about", "and", "book", "cover", "editing", "for",
    "ghostwriting", "hello", "help", "hi", "marketing", "my",
    "need", "price", "pricing", "publish", "the", "want", "website", "writing",
    # Common English function words that appear in short messages.
    # Prevents lingua from misclassifying short all-ASCII sentences like
    # "I'm in EST timezone." or "Yes please book it."
    "i", "i'm", "i've", "i'll", "i'd", "in", "is", "it", "it's",
    "am", "are", "at", "be", "been", "by", "can", "could",
    "do", "does", "from", "get", "have", "how", "if",
    "just", "like", "me", "more", "not", "of", "on", "or",
    "out", "please", "so", "that", "this", "to", "up",
    "was", "we", "what", "when", "will", "with", "would",
    "yes", "you", "your",
    # Common timezone/scheduling words to prevent misclassification
    "timezone", "est", "cst", "pst", "gmt", "utc", "morning",
    "afternoon", "evening", "friday", "monday", "tuesday", "wednesday",
    "thursday", "saturday", "sunday",
    # Domain-specific single-word queries that Lingua misclassifies because
    # the word exists in multiple Romance languages ("Consultation" = French,
    # "Schedule" = common to several languages, etc.)
    "consultation", "schedule", "scheduled", "scheduling",
    "available", "appointment", "specialist", "formatting",
    "sure", "okay", "ok", "thanks", "great", "perfect", "sounds",
    "tomorrow", "today", "now", "later", "soon", "anytime", "flexible",
    "correct", "exactly", "absolutely", "definitely", "certainly",
    "no", "nope", "yep", "yeah", "alright", "right",
}


@dataclass(slots=True)
class LanguageGuard:
    enabled: bool = True

    def detect(self, text: str, cached_language: str | None = None) -> LanguageDecision:
        started = time.perf_counter()
        if not self.enabled:
            return self._record("en", True, 1.0, "disabled", started)

        stripped = text.strip()
        # Short-message bypass: single words and very short replies ("Consultation?",
        # "Yes please", "OK", "Sure", "Tomorrow") are too brief for reliable language
        # detection — inherit the session's cached language instead.
        # Threshold raised from 12 to 25 to cover single domain-specific words.
        if len(stripped) < 25:
            language = cached_language or "en"
            return self._record(language, language == "en", 0.9, "short_message", started)

        # Step 14: detect Roman Urdu / Hinglish book-service intent before hard redirect.
        # These are ASCII (Roman script) so they pass the ascii_ratio check but get
        # misclassified by lingua as non-English. We route them as English-adjacent.
        if _ROMAN_URDU_INTENT_RE.search(stripped):
            return self._record("en", True, 0.8, "roman_urdu_lead_bypass", started)

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
