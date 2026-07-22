import re
import time
from dataclasses import dataclass

import structlog
from prometheus_client import Counter, Histogram

from bookcraft.components.language_guard.models import LanguageDecision
from bookcraft.components.language_guard.pii_masking import is_predominantly_pii, mask_pii

# ---------------------------------------------------------------------------
# Roman Urdu / Hindi (transliterated) detection
# ---------------------------------------------------------------------------
# Policy: BookCraft support is ENGLISH-ONLY. Roman Urdu / Hinglish is Latin script,
# so `lingua` (which has no Urdu and no transliteration model) classifies it
# unreliably — short messages leaked through as "English" and got answered (the bot
# even mirrored the customer's Urdu), while longer ones tripped the redirect. That
# flip-flop (chat 6685) is fixed by detecting transliterated Urdu/Hindi directly,
# length-independently, and redirecting it consistently BEFORE any bypass.
#
# These are common Roman Urdu/Hindi function words that rarely appear in English.
# Anything that overlaps ENGLISH_HINTS is filtered out at match time, so a token
# like "hi" (an English hint) never counts as an Urdu marker.
_ROMAN_URDU_MARKERS = frozenset(
    {
        # to-be / questions
        "hai", "hain", "hoo", "hun", "kya", "kia", "kyun", "kyu", "kaisa", "kaise",
        "kaisi", "kahan", "kahaan", "kaun", "kon", "kitna", "kitni", "kitne",
        # pronouns / possessives
        "mera", "meri", "mere", "tera", "teri", "tere", "tum", "tumhe", "tumhein",
        "tumhara", "tumhari", "aap", "apka", "apki", "apko", "hum", "humein",
        "hamara", "hamari", "mein", "mujhe", "mujhko", "usko", "unko",
        # negation / affirmation
        "nahi", "nahin", "nai", "acha", "accha", "achi", "achha", "theek", "bilkul",
        # verbs
        "chalo", "chaliye", "karo", "karna", "karni", "karein", "karta", "karti",
        "karte", "kar", "raha", "rahe", "rahi", "rha", "rhe", "rahay", "kehrahay",
        "keh", "kaha", "bol", "bolo", "batao", "bata", "batayein", "likha", "likhli",
        "likhi", "dena", "denan", "chahiye", "chahiyay", "karwani", "karwana",
        # common adverbs / connectors / nouns
        "kuch", "kuchh", "sab", "phir", "phr", "wapas", "abhi", "filhaal", "yaar",
        "bhai", "behen", "sahab", "sahib", "saheb", "matlab", "wala", "wali",
        "waisay", "waise", "thoda", "zyada", "ziada", "bohat", "bahut", "buhat",
        "yeh", "woh", "wohi", "kyunki", "magar", "lekin", "agar", "warna", "aur",
        "naam", "daftar", "kitab", "kitaab", "baat", "baray", "pagal", "insaan",
        "hee", "urdu", "angrezi", "angerezi", "jawab", "baap", "pehle",
        "choro", "chhoro", "karlo", "ghora", "ghoray", "masnoi", "zahanat",
    }
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

        # Roman Urdu / Hindi (transliterated) is detected FIRST, at any length, and
        # redirected consistently — English-only policy. This runs before the
        # short-message bypass so brief Urdu ("acha", "kia kehrahay hoo") can no
        # longer slip through as English and get answered/mirrored (chat 6685).
        if self._is_roman_urdu(stripped):
            return self._record("ur", False, 0.9, "roman_urdu", started)

        # PII/contact bypass: messages that are predominantly contact info must not be
        # rejected as non-English (names, emails, phone numbers are language-neutral).
        if is_predominantly_pii(stripped):
            return self._record("en", True, 0.95, "pii_bypass", started)

        # Short-message bypass: single words and very short replies ("Consultation?",
        # "Yes please", "OK", "Sure", "Tomorrow") are too brief for reliable language
        # detection. A short message carrying an English hint stays English; otherwise
        # it inherits the session's cached language.
        if len(stripped) < 25:
            if set(re.findall(r"[a-z']+", stripped.lower())) & ENGLISH_HINTS:
                return self._record("en", True, 0.9, "short_message_en", started)
            language = cached_language or "en"
            return self._record(language, language == "en", 0.9, "short_message", started)

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
    def _is_roman_urdu(text: str) -> bool:
        """Detect transliterated (Roman-script) Urdu/Hindi, length-independent.

        Fires when the message carries enough Roman-Urdu function words to be
        unmistakable, while staying quiet on short English replies. Tokens that are
        also English hints (e.g. "hi") never count as Urdu markers.
        """
        tokens = re.findall(r"[a-zA-Z']+", text.lower())
        if not tokens:
            return False
        markers = [t for t in tokens if t in _ROMAN_URDU_MARKERS and t not in ENGLISH_HINTS]
        marker_count = len(markers)
        if marker_count >= 2:  # noqa: PLR2004
            return True
        # A single unambiguous marker is enough only when it dominates a very short
        # message ("acha", "likhli hai") — never for one stray word in a long English
        # sentence, which the ascii/lingua paths below still handle.
        if marker_count == 1 and (len(tokens) <= 2 or marker_count / len(tokens) >= 0.5):  # noqa: PLR2004
            return True
        return False

    @staticmethod
    def _ascii_ratio(text: str) -> float:
        if not text:
            return 1.0
        ascii_chars = sum(1 for char in text if ord(char) < 128)
        return ascii_chars / len(text)
