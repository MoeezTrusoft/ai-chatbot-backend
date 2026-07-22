import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from prometheus_client import Counter, Histogram

from bookcraft.components.preprocessor.detectors import (
    detect_book_format,
    detect_genre,
    detect_genre_uncertainty,
    detect_greeting_only,
    detect_manuscript_status,
    has_agreement_request,
    has_nda_request,
    has_pricing_intent,
    split_questions,
)
from bookcraft.components.preprocessor.embedding import EmbeddingClient
from bookcraft.components.preprocessor.negation_targets import NegationTargetResolver
from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span, TokenInfo
from bookcraft.components.preprocessor.sidecars import PreprocessorSidecars
from bookcraft.domain.enums import ServiceCategory

_NEGATION_RESOLVER = NegationTargetResolver()

# ---------------------------------------------------------------------------
# Written-number helpers for word/page count extraction
# ---------------------------------------------------------------------------

_ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_MAGNITUDES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}

# Matches written number phrases like "eighty-five thousand" or "three hundred thousand".
_WRITTEN_NUMBER_RE = re.compile(
    r"\b(?:"
    r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[\s-]"
    r"(?:one|two|three|four|five|six|seven|eight|nine)|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"
    r"(?:\s+(?:hundred|thousand|million))*"
    r"(?:\s+(?:and\s+)?(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen))??"
    r")\b",
    re.IGNORECASE,
)


def _parse_written_number(text: str) -> int | None:
    """Convert an English number phrase to an integer.

    Handles: "three thousand", "eighty-five thousand", "two hundred",
    "one hundred thousand", "three million". Returns None if unparseable.
    Max supported value: 999,999,999.
    """
    text = text.lower().replace("-", " ").replace(",", "").strip()
    words = text.split()
    total = 0
    current = 0
    for word in words:
        if word == "and":
            continue
        if word in _ONES:
            current += _ONES[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word == "thousand":
            total += (current or 1) * 1_000
            current = 0
        elif word == "million":
            total += (current or 1) * 1_000_000
            current = 0
        else:
            return None  # Unknown word — bail out
    total += current
    return total if total > 0 else None


_NUM_WORDS = frozenset(
    "zero one two three four five six seven eight nine ten "
    "eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen "
    "twenty thirty forty fifty sixty seventy eighty ninety "
    "hundred thousand million and".split()
)


def _extract_written_counts(text: str) -> tuple[list[int], list[int]]:
    """Extract word/page counts expressed as English number words followed by 'words'/'pages'.

    Strategy: scan for 'words' and 'pages' label words, then walk backwards
    to collect all adjacent English number tokens and parse them as a whole phrase.
    This correctly handles "twelve thousand five hundred words" → 12500.

    Returns (word_counts, page_counts).
    """
    written_word: list[int] = []
    written_page: list[int] = []

    for label, target in ((r"words?\b", "word"), (r"pages?\b", "page")):
        for m in re.finditer(label, text, re.IGNORECASE):
            # Collect tokens immediately before the label (up to 12 tokens back).
            prefix = text[: m.start()].strip()
            tokens = re.split(r"[\s\-,]+", prefix)
            phrase_tokens: list[str] = []
            for tok in reversed(tokens[-12:]):
                tok_lower = tok.lower().rstrip("s")  # "hundreds" → "hundred"
                if tok_lower in _NUM_WORDS or tok.lower() in _NUM_WORDS:
                    phrase_tokens.insert(0, tok.lower())
                else:
                    break  # Non-number word — stop collecting
            if not phrase_tokens:
                continue
            candidate = " ".join(phrase_tokens)
            n = _parse_written_number(candidate)
            if n is not None and n > 0:
                if target == "word":
                    written_word.append(n)
                else:
                    written_page.append(n)

    return written_word, written_page


PREPROCESSOR_SECONDS = Histogram("preprocessor_seconds", "Preprocessor latency.")
ATOMS_EXTRACTED = Counter(
    "preprocessor_atoms_extracted_total",
    "Deterministic atoms extracted.",
    ["atom_type"],
)
NEGATION_SPANS_TOTAL = Counter("preprocessor_negation_spans_total", "Negation spans detected.")
HEDGE_SPANS_TOTAL = Counter("preprocessor_hedge_spans_total", "Hedge spans detected.")
COUNTERFACTUAL_SPANS_TOTAL = Counter(
    "preprocessor_counterfactual_spans_total",
    "Counterfactual spans detected.",
)

SERVICE_KEYWORDS = {
    ServiceCategory.GHOSTWRITING: [
        "ghostwriting",
        "ghost writer",
        "ghost-writer",
        "write my book",
        "writing my book",
        "writing the story",
        "help writing",
        "help writing the story",
        "help me write",
        "story writing",
        "only have an idea",
        "idea for a children’s",
        "idea for a children’s",
        "children’s picture book",
        "children’s picture book",
    ],
    ServiceCategory.EDITING_PROOFREADING: ["editing", "proofreading", "proofread"],
    ServiceCategory.COVER_DESIGN_ILLUSTRATION: [
        # Explicit cover-design phrases — no standalone "cover" (Step 3)
        "cover design",
        "professional cover",
        "book cover",
        "front cover",
        "cover for my book",
        "cover artwork",
        "cover art",
        "cover illustration",
        "cover designer",
        "cover design service",
        # Illustration phrases — NOT writing-related
        "illustrations",
        "creating illustrations",
        "picture book illustrations",
        "children’s book illustrations",
        "children’s book illustrations",
        "illustrator",
        "book illustration",
        "illustration service",
        # Children’s-specific illustration (only when illustration-context present)
        "children’s picture book illustration",
        "children’s picture book illustration",
    ],
    ServiceCategory.INTERIOR_FORMATTING: [
        "interior formatting",
        "formatting",
        "typesetting",
        "book layout",
        "interior layout",
        "page layout",
        "layout breaking",
        "layout",
        "recipe tables",
        "ingredient lists",
        "section dividers",
        "paperback and kindle",
        "print and kindle",
        "kindle and paperback",
        "print-ready",
        "print ready",
        "formatting it for print and kindle",
        "format it for print and kindle",
        "print and Kindle",
    ],
    ServiceCategory.AUDIOBOOK_PRODUCTION: ["audiobook", "audio book", "narration"],
    ServiceCategory.PUBLISHING_DISTRIBUTION: [
        "publishing",
        "distribution",
        "amazon",
        "kdp",
        "ingramspark",
        "publishing it on amazon",
        "publishing it on Amazon",
        "publish it on amazon",
        "publish it on Amazon",
        "amazon publishing",
        "Amazon publishing",
    ],
    ServiceCategory.MARKETING_PROMOTION: ["marketing", "promotion", "ads", "campaign", "launch"],
    ServiceCategory.AUTHOR_WEBSITE: ["author website", "website"],
    ServiceCategory.VIDEO_TRAILER: ["video trailer", "trailer"],
    ServiceCategory.FINE_ART_MONOGRAPH: [
        "art book",
        "fine art book",
        "coffee table book",
        "monograph",
        "photography book",
        "gallery book",
        "art monograph",
        "collector's book",
        "fine-art book",
        "art photography book",
    ],
    ServiceCategory.CATALOG_TRANSITION: [
        "publisher is closing",
        "publisher closed",
        "move my catalog",
        "transfer my books",
        "leaving my publisher",
        "rights back",
        "get my rights",
        "backlist transfer",
        "catalog handover",
        "my publisher is shutting",
        "publisher going out of business",
    ],
    ServiceCategory.PUBLISHING_PARTNERSHIP: [
        "publishing partner",
        "ongoing partnership",
        "hybrid publishing",
        "full service publishing",
        "publish my whole catalog",
        "long term publisher",
        "ongoing publishing",
    ],
    ServiceCategory.AUTHOR_BRAND_PLATFORM: [
        "author brand",
        "author platform",
        "build my audience",
        "grow my newsletter",
        "build a following",
        "author website and brand",
        "build my newsletter",
        "author presence",
    ],
    ServiceCategory.TRANSLATION_FOREIGN_RIGHTS: [
        "translate my book",
        "foreign rights",
        "foreign edition",
        "other languages",
        "international rights",
        "localize my book",
        "translated edition",
        "publish in spanish",
        "publish in french",
        "publish in german",
    ],
    ServiceCategory.SPECIAL_COLLECTOR_EDITIONS: [
        "collector edition",
        "collectors edition",
        "limited edition",
        "signed edition",
        "boxed set",
        "box set",
        "deluxe edition",
        "special edition",
        "anniversary edition",
        "numbered edition",
        "slipcase edition",
    ],
}

CUE_SPAN_WINDOW_CHARS = 80
CUE_TERMINATOR_RE = re.compile(
    r"[,.!?;]|\b(?:but|however|instead|rather|except|unless|although)\b",
    flags=re.IGNORECASE,
)
NEGATION_TERMINATOR_RE = re.compile(
    r"[.!?;]|\b(?:but|however|instead|rather|except|unless|although)\b",
    flags=re.IGNORECASE,
)
NEGATION_CUES_WITH_LIST_SCOPE = {
    "no",
    "not",
    "never",
    "without",
    "do not",
    "don't",
    "does not",
    "doesn't",
    "did not",
    "didn't",
    "cannot",
    "can't",
}
BACKWARD_NEGATION_RE = re.compile(
    r"\b(?P<subject>quote|pricing|price|timeline|agreement|contract|nda|"
    r"document|documents|manuscript|service|scope)\b"
    r"\s+(?:is|are|was|were|be|being|been)\s+"
    r"(?:not|never|no longer|n't)\b",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class SharedPreprocessor:
    sidecars: PreprocessorSidecars
    embedding_client: EmbeddingClient

    async def process(self, raw: str, language: str = "en") -> ProcessedMessage:
        with PREPROCESSOR_SECONDS.time():
            normalized = self._normalize(raw)
            tokens = self._tokenize(normalized)
            negation_spans = self._negation_spans(normalized)
            hedge_spans = self._cue_spans(normalized, self.sidecars.hedge_cues)
            counterfactual_spans = self._cue_spans(normalized, self.sidecars.counterfactual_cues)
            tokens = self._mark_tokens(tokens, negation_spans, hedge_spans, counterfactual_spans)
            atoms = self._deterministic_atoms(
                normalized,
                negation_spans=negation_spans,
                hedge_spans=hedge_spans,
                counterfactual_spans=counterfactual_spans,
            )
            embedding = await self.embedding_client.embed(normalized, language)
            negation_resolution = _NEGATION_RESOLVER.resolve(
                text=normalized,
                negation_spans=negation_spans,
                counterfactual_spans=counterfactual_spans,
            )
            NEGATION_SPANS_TOTAL.inc(len(negation_spans))
            HEDGE_SPANS_TOTAL.inc(len(hedge_spans))
            COUNTERFACTUAL_SPANS_TOTAL.inc(len(counterfactual_spans))
            return ProcessedMessage(
                raw=raw,
                normalized=normalized,
                tokens=tokens,
                negation_spans=negation_spans,
                hedge_spans=hedge_spans,
                counterfactual_spans=counterfactual_spans,
                deterministic_atoms=atoms,
                embedding=embedding,
                language=language,
                char_count=len(raw),
                negation_targets=negation_resolution.targets,
            )

    def _normalize(self, raw: str) -> str:
        normalized = unicodedata.normalize("NFKC", raw)
        for source, target in self.sidecars.typography_replacements.items():
            normalized = normalized.replace(source, target)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        for source, target in self.sidecars.compound_variants.items():
            normalized = re.sub(re.escape(source), target, normalized, flags=re.IGNORECASE)
        return normalized

    @staticmethod
    def _tokenize(text: str) -> list[TokenInfo]:
        return [
            TokenInfo(
                text=match.group(0),
                lemma=match.group(0).lower(),
                start=match.start(),
                end=match.end(),
            )
            for match in re.finditer(r"\b[\w']+\b", text)
        ]

    def _negation_spans(self, text: str) -> list[Span]:
        spans = self._cue_spans(text, self.sidecars.negation_cues)
        spans.extend(self._backward_negation_spans(text))
        return _dedupe_spans(spans)

    @staticmethod
    def _cue_spans(text: str, cues: list[str]) -> list[Span]:
        spans: list[Span] = []
        for cue in cues:
            for match in re.finditer(rf"\b{re.escape(cue)}\b", text, flags=re.IGNORECASE):
                end = (
                    _negation_span_end(text, match.end())
                    if cue.casefold() in NEGATION_CUES_WITH_LIST_SCOPE
                    else _cue_span_end(text, match.end())
                )
                spans.append(
                    Span(start=match.start(), end=end, text=text[match.start() : end], cue=cue)
                )
        return _dedupe_spans(spans)

    @staticmethod
    def _backward_negation_spans(text: str) -> list[Span]:
        spans: list[Span] = []
        for match in BACKWARD_NEGATION_RE.finditer(text):
            end = _cue_span_end(text, match.end())
            spans.append(
                Span(
                    start=match.start("subject"),
                    end=end,
                    text=text[match.start("subject") : end],
                    cue="backward_negation",
                )
            )
        return spans

    @staticmethod
    def _mark_tokens(
        tokens: list[TokenInfo],
        negation_spans: list[Span],
        hedge_spans: list[Span],
        counterfactual_spans: list[Span],
    ) -> list[TokenInfo]:
        marked: list[TokenInfo] = []
        for token in tokens:
            marked.append(
                token.model_copy(
                    update={
                        "negated": _overlaps(token, negation_spans),
                        "hedged": _overlaps(token, hedge_spans),
                        "counterfactual": _overlaps(token, counterfactual_spans),
                    }
                )
            )
        return marked

    def _deterministic_atoms(
        self,
        text: str,
        negation_spans: list[Span] | None = None,
        hedge_spans: list[Span] | None = None,
        counterfactual_spans: list[Span] | None = None,
    ) -> dict[str, object]:
        atoms: dict[str, object] = {}
        negation_spans = negation_spans or []
        hedge_spans = hedge_spans or []
        counterfactual_spans = counterfactual_spans or []

        self._put_all(atoms, "emails", re.findall(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text))
        self._put_all(atoms, "urls", re.findall(r"https?://\S+", text))
        self._put_all(atoms, "phones", re.findall(r"(?:\+?\d[\d\s().-]{7,}\d)", text))
        self._put_all(atoms, "currency", re.findall(r"(?:\$|USD\s*)\d[\d,]*(?:\.\d+)?", text))
        word_counts = [
            int(value.replace(",", ""))
            for value in re.findall(r"(\d[\d,]*)\s+words?\b", text, re.I)
        ]
        page_counts = [
            int(value.replace(",", ""))
            for value in re.findall(r"(\d[\d,]*)\s+pages?\b", text, re.I)
        ]
        # Also extract English number words: "three thousand words", "eighty-five thousand pages".
        _written_word, _written_page = _extract_written_counts(text)
        for _n in _written_word:
            if _n not in word_counts:
                word_counts.append(_n)
        for _n in _written_page:
            if _n not in page_counts:
                page_counts.append(_n)
        self._put_all(atoms, "word_counts", word_counts)
        self._put_all(atoms, "page_counts", page_counts)
        service_mentions = _service_mentions(
            text,
            negation_spans=negation_spans,
            hedge_spans=hedge_spans,
            counterfactual_spans=counterfactual_spans,
        )
        services = _ordered_unique(
            mention["service"] for mention in service_mentions if not mention["negated"]
        )
        negated_services = _ordered_unique(
            mention["service"] for mention in service_mentions if mention["negated"]
        )

        self._put_all(atoms, "service_mentions", service_mentions)
        self._put_all(atoms, "services", services)
        self._put_all(atoms, "negated_services", negated_services)
        # Every distinct question in the turn. Downstream this drives per-question RAG
        # retrieval and the "answer all of them" prompt contract, so a checklist paste
        # is not reduced to whichever question a regex happened to match first.
        self._put_all(atoms, "questions", split_questions(text))
        self._put_all(atoms, "negated_terms", _negated_terms(text, negation_spans))
        self._put_all(atoms, "context_markers", _context_markers(text, counterfactual_spans))
        self._put_all(atoms, "forbid_markers", _forbid_markers(text, negation_spans=negation_spans))
        self._put_all(
            atoms,
            "query_cues",
            _query_cues(
                text,
                negation_spans=negation_spans,
                counterfactual_spans=counterfactual_spans,
            ),
        )
        status = detect_manuscript_status(
            text,
            negation_spans=negation_spans,
            counterfactual_spans=counterfactual_spans,
        )
        if status:
            atoms["manuscript_status"] = status.value
            ATOMS_EXTRACTED.labels(atom_type="manuscript_status").inc()

        # Genre uncertainty detection — must precede confirmed genre extraction.
        # If the user expresses uncertainty, we record candidates but do NOT confirm genre.
        uncertainty = detect_genre_uncertainty(text)
        if uncertainty.uncertain:
            atoms["genre_status"] = "uncertain"
            ATOMS_EXTRACTED.labels(atom_type="genre_status").inc()
            if uncertainty.genre_candidates:
                atoms["genre_candidates"] = uncertainty.genre_candidates
                ATOMS_EXTRACTED.labels(atom_type="genre_candidates").inc()
            if uncertainty.negated_genres:
                atoms["negated_genres"] = uncertainty.negated_genres
        else:
            genre = detect_genre(text)
            if genre:
                atoms["genre"] = genre
                ATOMS_EXTRACTED.labels(atom_type="genre").inc()

        # Book format detection — 'picture book' is a format, not a genre/audience.
        book_format = detect_book_format(text)
        if book_format.book_formats:
            atoms["book_formats"] = book_format.book_formats
            ATOMS_EXTRACTED.labels(atom_type="book_formats").inc()
        if book_format.audience:
            atoms["audience"] = book_format.audience
            ATOMS_EXTRACTED.labels(atom_type="audience").inc()

        # Greeting intent guard — greeting-only turns must not trigger scoping.
        greeting = detect_greeting_only(text)
        if greeting.is_greeting_only:
            atoms["is_greeting_only"] = True
            ATOMS_EXTRACTED.labels(atom_type="is_greeting_only").inc()

        return atoms

    @staticmethod
    def _put_all(atoms: dict[str, object], key: str, values: Sequence[object]) -> None:
        if values:
            atoms[key] = values
            ATOMS_EXTRACTED.labels(atom_type=key).inc(len(values))


def _overlaps(token: TokenInfo, spans: list[Span]) -> bool:
    return any(token.start < span.end and token.end > span.start for span in spans)


def _negation_span_end(text: str, cue_end: int) -> int:
    window_end = min(len(text), cue_end + CUE_SPAN_WINDOW_CHARS)
    window = text[cue_end:window_end]
    terminator = NEGATION_TERMINATOR_RE.search(window)
    if terminator is None:
        return window_end

    end = cue_end + terminator.start()
    if terminator.group(0) in {".", "!", "?", ";"}:
        end += 1
    return max(cue_end, end)


def _cue_span_end(text: str, cue_end: int) -> int:
    window_end = min(len(text), cue_end + CUE_SPAN_WINDOW_CHARS)
    window = text[cue_end:window_end]
    terminator = CUE_TERMINATOR_RE.search(window)
    if terminator is None:
        return window_end

    end = cue_end + terminator.start()
    if terminator.group(0) in {".", "!", "?", ";"}:
        end += 1
    return max(cue_end, end)


def _dedupe_spans(spans: list[Span]) -> list[Span]:
    unique: dict[tuple[int, int, str], Span] = {}
    for span in spans:
        unique[(span.start, span.end, span.cue)] = span
    return sorted(unique.values(), key=lambda item: (item.start, item.end, item.cue))


def _service_mentions(
    text: str,
    negation_spans: list[Span],
    hedge_spans: list[Span],
    counterfactual_spans: list[Span],
) -> list[dict[str, Any]]:
    lowered = text.lower()
    mentions: list[dict[str, Any]] = []

    for service, keywords in SERVICE_KEYWORDS.items():
        for keyword in keywords:
            for match in re.finditer(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)", lowered):
                mentions.append(
                    {
                        "service": service.value,
                        "keyword": text[match.start() : match.end()],
                        "start": match.start(),
                        "end": match.end(),
                        "negated": _span_overlaps(match.start(), match.end(), negation_spans),
                        "hedged": _span_overlaps(match.start(), match.end(), hedge_spans),
                        "counterfactual": _span_overlaps(
                            match.start(),
                            match.end(),
                            counterfactual_spans,
                        ),
                    }
                )

    mentions.sort(key=lambda item: (int(item["start"]), str(item["service"])))
    return _dedupe_service_mentions(mentions)


def _dedupe_service_mentions(mentions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int, int]] = set()
    unique: list[dict[str, Any]] = []
    for mention in mentions:
        key = (str(mention["service"]), int(mention["start"]), int(mention["end"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(mention)
    return unique


def _ordered_unique(values: Iterable[object]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        item = str(value)
        if item not in ordered:
            ordered.append(item)
    return ordered


def _span_overlaps(start: int, end: int, spans: list[Span]) -> bool:
    return any(start < span.end and end > span.start for span in spans)


def _negated_terms(text: str, negation_spans: list[Span]) -> list[str]:
    terms = ["quote", "pricing", "timeline", "agreement", "contract", "nda", "payment"]
    lowered = text.casefold()
    found: list[str] = []

    def add(term: str) -> None:
        if term not in found:
            found.append(term)

    for term in terms:
        start = lowered.find(term)
        if start < 0:
            continue

        end = start + len(term)
        if _span_overlaps(start, end, negation_spans):
            add(term)

    backward_pattern = re.compile(
        r"\b(?P<term>quote|pricing|timeline|agreement|contract|nda|payment)\s+"
        r"(?:is|are|was|were)\s+not\s+"
        r"(?:finalized|approved|ready|confirmed|included)\b",
        flags=re.IGNORECASE,
    )
    for match in backward_pattern.finditer(text):
        add(match.group("term").casefold())

    return found


def _context_markers(text: str, counterfactual_spans: list[Span]) -> list[str]:
    lowered = text.casefold()
    markers: list[str] = []

    def add(value: str) -> None:
        if value not in markers:
            markers.append(value)

    if counterfactual_spans:
        add("counterfactual")

    if any(phrase in lowered for phrase in ("bestseller", "promise", "guarantee")):
        add("guarantee_pressure")

    if any(phrase in lowered for phrase in ("blank pricing", "filled later", "skip the quote")):
        add("pricing_gate")

    if any(phrase in lowered for phrase in ("sign the agreement", "agreement today", "contract")):
        add("contract_pressure")

    if any(phrase in lowered for phrase in ("http://", "https://", "fake sample links")):
        add("unsafe_user_supplied_link")

    if any(phrase in lowered for phrase in ("file types", "upload")):
        add("upload_safety")

    if any(phrase in lowered for phrase in ("avoid sharing", "privacy")):
        add("privacy")

    if any(phrase in lowered for phrase in ("fake reviews", "no fake reviews")):
        add("review_policy_safety")

    return markers


# Word-anchored: the previous bare `"promise" in text` test also fired on "compromise"
# and "compromised", so an author asking whether a clause would COMPROMISE their
# copyright was answered with an unprompted speech about bestseller ranks (chat 5876).
_GUARANTEE_DEMAND_RE = re.compile(
    r"\b(?:guarantee|guarantees|guaranteed|guaranteeing|"
    r"promise|promises|promised|promising|"
    r"bestseller|best[\s-]seller|number\s+one\s+(?:spot|rank|ranking)|"
    r"money[\s-]back|satisfaction\s+guaranteed)\b",
    re.IGNORECASE,
)


def _demands_guarantee(text: str, *, negation_spans: list[Span] | None = None) -> bool:
    """True only when the author is actually pressing US for a guarantee.

    A bare keyword hit is not enough: "I'm not asking for a guarantee" mentions one
    without wanting one, and answering it with the bestseller disclaimer is a non
    sequitur. Negated mentions are therefore skipped.

    Counterfactual mentions are NOT skipped. "If I signed today, would you promise a
    bestseller campaign?" is a real guarantee demand wearing a hypothetical — it is
    exactly the pressure this marker exists to catch.
    """
    negated = list(negation_spans or [])
    for match in _GUARANTEE_DEMAND_RE.finditer(text):
        if _span_overlaps(match.start(), match.end(), negated):
            continue
        return True
    return False


def _forbid_markers(text: str, *, negation_spans: list[Span] | None = None) -> list[str]:
    lowered = text.casefold()
    markers: list[str] = []

    def add(value: str) -> None:
        if value not in markers:
            markers.append(value)

    if any(phrase in lowered for phrase in ("40 percent", "cut the price", "price by")):
        add("price_number")

    if _demands_guarantee(text, negation_spans=negation_spans):
        add("guarantee")

    if any(phrase in lowered for phrase in ("blank pricing", "filled later", "skip the quote")):
        add("agreement_generation_without_quote")

    if any(phrase in lowered for phrase in ("fake sample links", "http://", "https://")):
        add("fake_link_acceptance")

    return markers


def _query_cues(
    text: str,
    *,
    negation_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> list[str]:
    lowered = text.casefold()
    cues: list[str] = []

    def add(value: str) -> None:
        if value not in cues:
            cues.append(value)

    if has_agreement_request(
        text,
        negation_spans=negation_spans,
        counterfactual_spans=counterfactual_spans,
    ):
        add("agreement_request")

    if has_pricing_intent(
        text,
        negation_spans=negation_spans,
        counterfactual_spans=counterfactual_spans,
    ):
        add("pricing_question")

    if has_nda_request(
        text,
        negation_spans=negation_spans,
        counterfactual_spans=counterfactual_spans,
    ):
        add("nda_request")

    if any(phrase in lowered for phrase in ("portfolio", "sample", "samples")):
        add("portfolio_request")

    if any(phrase in lowered for phrase in ("file types", "upload", "services")):
        add("service_question")

    return cues
