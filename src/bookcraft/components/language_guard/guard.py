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
        # Transliteration variants that leaked through (chat: "kia haal he kese ho?").
        # Roman Urdu has no fixed spelling, so common respellings must be covered.
        "kese", "kesay", "kaisay", "kesa", "kesi", "kesy", "kese", "haal",
        "hou", "houn", "kro", "krna", "krni", "kren", "krein", "krlo",
        "kyunke", "kyunkay", "kripya", "shukriya", "shukria", "meharbani",
        "thek", "thik", "kaha", "kahan", "kaisay", "batana", "samajh", "samjha",
        "chahye", "chahiya", "hoga", "hogi", "hoge", "tha", "thi", "thay",
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


# English GRAMMATICAL function words — signal a genuine English clause, unlike domain
# loanwords ("price", "editing", "cover") that routinely appear inside otherwise-Urdu
# sentences ("Price kya hai editing ka?"). Mixed-language detection requires a real
# English clause (>= 2 of these), so a Roman-Urdu sentence sprinkled with English nouns
# still gets the clean English-only redirect rather than being treated as "mixed".
_ENGLISH_STRUCTURAL = frozenset(
    {
        "i", "i'm", "i've", "i'll", "i'd", "me", "my", "we", "our", "you", "your",
        "he", "she", "they", "it", "this", "that", "these", "those",
        "can", "could", "would", "will", "should", "do", "does", "did", "is", "are",
        "am", "be", "been", "was", "were", "have", "has", "had", "want", "need",
        "help", "get", "make", "tell", "give", "know", "like", "looking",
        "what", "how", "when", "where", "why", "who", "which",
        "the", "a", "an", "to", "with", "for", "from", "of", "in", "on",
        "and", "or", "if", "please", "about",
    }
)

# Non-Latin writing systems, keyed by ISO 639-1. BookCraft support is English-only,
# but `lingua` (below) is built with only a handful of languages and silently defaults
# any script it wasn't given — notably Gujarati — to English, so short non-Latin
# messages ("કેમ છો?") slipped through as English and got answered. We catch these by
# Unicode block FIRST, independent of lingua and independent of message length. Each
# entry is (iso, low_codepoint, high_codepoint).
_NON_LATIN_SCRIPT_RANGES = (
    ("gu", 0x0A80, 0x0AFF),   # Gujarati
    ("hi", 0x0900, 0x097F),   # Devanagari (Hindi/Marathi/Nepali)
    ("bn", 0x0980, 0x09FF),   # Bengali/Assamese
    ("pa", 0x0A00, 0x0A7F),   # Gurmukhi (Punjabi)
    ("or", 0x0B00, 0x0B7F),   # Odia
    ("ta", 0x0B80, 0x0BFF),   # Tamil
    ("te", 0x0C00, 0x0C7F),   # Telugu
    ("kn", 0x0C80, 0x0CFF),   # Kannada
    ("ml", 0x0D00, 0x0D7F),   # Malayalam
    ("si", 0x0D80, 0x0DFF),   # Sinhala
    ("th", 0x0E00, 0x0E7F),   # Thai
    ("ar", 0x0600, 0x06FF),   # Arabic
    ("fa", 0x0750, 0x077F),   # Arabic Supplement (Persian/Urdu extras)
    ("he", 0x0590, 0x05FF),   # Hebrew
    ("ru", 0x0400, 0x04FF),   # Cyrillic
    ("el", 0x0370, 0x03FF),   # Greek
    ("hy", 0x0530, 0x058F),   # Armenian
    ("ka", 0x10A0, 0x10FF),   # Georgian
    ("ko", 0xAC00, 0xD7A3),   # Hangul syllables (Korean)
    ("ja", 0x3040, 0x30FF),   # Hiragana + Katakana (Japanese)
    ("zh", 0x4E00, 0x9FFF),   # CJK Unified Ideographs
)


# Process-wide lingua detector, built once and reused for every message. Rebuilding it
# per call (the previous behavior) reloaded the language models on every detection.
# It now spans ALL languages lingua supports (~75) in high-accuracy mode, so Latin-
# script languages (Spanish, French, ...) and any script we don't special-case are
# identified natively rather than being forced into a small fixed set (which silently
# defaulted e.g. Gujarati to English). Models load lazily on first use, so the resident
# set stays proportional to the languages actually seen — mostly English in practice.
# NB: the FIRST detection after a fresh process pays a one-time (~2 s) model-load cost;
# lingua is only reached for longer, non-ASCII / ambiguous messages, so this is rare.
_LINGUA_DETECTOR = None


def _get_lingua_detector():
    global _LINGUA_DETECTOR
    if _LINGUA_DETECTOR is None:
        from lingua import LanguageDetectorBuilder

        _LINGUA_DETECTOR = LanguageDetectorBuilder.from_all_languages().build()
    return _LINGUA_DETECTOR


@dataclass(slots=True)
class LanguageGuard:
    enabled: bool = True

    def detect(self, text: str, cached_language: str | None = None) -> LanguageDecision:
        started = time.perf_counter()
        if not self.enabled:
            return self._record("en", True, 1.0, "disabled", started)

        stripped = text.strip()

        # Mixed English + another language: the author wrote a real English request
        # AND some words in another language. Policy: answer the English part, then
        # politely ask for the rest in English — that reply is Claude-generated, so we
        # DON'T hard-redirect. Requires SUBSTANTIAL English (>= 2 distinct English
        # function words) that is at least as prominent as the foreign markers;
        # otherwise a mostly-Urdu message with one stray English word still gets the
        # clean redirect below.
        _mix_tokens = set(re.findall(r"[a-zA-Z']+", stripped.lower()))
        _urdu = len({t for t in _mix_tokens if t in _ROMAN_URDU_MARKERS and t not in ENGLISH_HINTS})
        _eng_struct = len({t for t in _mix_tokens if t in _ENGLISH_STRUCTURAL})
        if _urdu >= 1 and _eng_struct >= 2:  # noqa: PLR2004
            return self._record_mixed(started)

        # Roman Urdu / Hindi (transliterated) is detected FIRST, at any length, and
        # redirected consistently — English-only policy. This runs before the
        # short-message bypass so brief Urdu ("acha", "kia kehrahay hoo") can no
        # longer slip through as English and get answered/mirrored (chat 6685).
        if self._is_roman_urdu(stripped):
            return self._record("ur", False, 0.9, "roman_urdu", started)

        # Non-Latin script (Gujarati, Hindi, Bengali, Arabic, CJK, Cyrillic, ...).
        # Runs BEFORE the short-message bypass so a brief line like "કેમ છો?" is no
        # longer defaulted to English, and BEFORE lingua (which is built with only a
        # few languages and would misfile or drop most of these scripts). If the author
        # also wrote a substantial English clause we treat it as mixed (answer the
        # English part); otherwise it's the clean English-only redirect.
        _script = self._dominant_non_latin_script(stripped)
        if _script is not None:
            if _eng_struct >= 2:  # noqa: PLR2004
                return self._record_mixed(started)
            return self._record(_script, False, 0.9, "non_latin_script", started)

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
        detector = _get_lingua_detector()
        language = detector.detect_language_of(text)
        if language is None:
            return self._record("en", True, 0.5, "lingua_low_confidence", started)
        iso = language.iso_code_639_1.name.lower()
        is_english = iso == "en"
        return self._record(iso, is_english, 0.8, "lingua", started)

    def _record_mixed(self, started: float) -> LanguageDecision:
        """Record a mixed English+foreign message.

        Proceeds to normal (Claude) generation in English — `is_english=True` so the
        pipeline runs and the turn is NOT redirected — but carries `is_mixed=True` so
        chat.py injects a directive telling the model to answer the English part and
        ask for the rest in English. No template reply is authored here.
        """
        LANGUAGE_DETECTION_SECONDS.labels(source="mixed_language").observe(
            time.perf_counter() - started
        )
        LANGUAGE_DETECTION_RESULTS.labels(language="mixed").inc()
        return LanguageDecision(
            language="en",
            is_english=True,
            confidence=0.85,
            source="mixed_language",
            redirect_message=None,
            is_mixed=True,
        )

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
        english_hint_present = any(t in ENGLISH_HINTS for t in tokens)
        if marker_count >= 2:  # noqa: PLR2004
            return True
        if marker_count == 1:
            # A single unmistakable Roman-Urdu marker is enough when the message
            # carries NO English signal at all — a genuine English sentence almost
            # always contains at least one English function word ("i", "the", "you",
            # "is", "to", "help", "book"...). Zero English hints + an Urdu marker is a
            # strong non-English signal, length-independent. Also fire when the marker
            # dominates a very short message ("acha", "likhli hai").
            if not english_hint_present:
                return True
            if len(tokens) <= 2 or marker_count / len(tokens) >= 0.5:  # noqa: PLR2004
                return True
        return False

    @staticmethod
    def _dominant_non_latin_script(text: str) -> str | None:
        """Return the ISO 639-1 code of a non-Latin script that DOMINATES the text.

        Looks only at alphabetic characters and maps each to a writing system by
        Unicode block. Returns the top non-Latin script only when it accounts for at
        least half of the letters, so a stray foreign glyph inside an English sentence
        doesn't trigger a redirect while a genuinely Gujarati/Arabic/CJK message does.
        Returns None for Latin-script text (English, Roman-Urdu, accented European),
        which continues down the normal detection path.
        """
        letters = [ch for ch in text if ch.isalpha()]
        if not letters:
            return None
        counts: dict[str, int] = {}
        for ch in letters:
            cp = ord(ch)
            for iso, lo, hi in _NON_LATIN_SCRIPT_RANGES:
                if lo <= cp <= hi:
                    counts[iso] = counts.get(iso, 0) + 1
                    break
        if not counts:
            return None
        top_iso, top_count = max(counts.items(), key=lambda kv: kv[1])
        if top_count / len(letters) >= 0.5:  # noqa: PLR2004
            return top_iso
        return None

    @staticmethod
    def _ascii_ratio(text: str) -> float:
        if not text:
            return 1.0
        ascii_chars = sum(1 for char in text if ord(char) < 128)
        return ascii_chars / len(text)
