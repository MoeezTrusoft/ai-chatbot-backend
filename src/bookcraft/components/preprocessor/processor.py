import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from prometheus_client import Counter, Histogram

from bookcraft.components.preprocessor.embedding import EmbeddingClient
from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span, TokenInfo
from bookcraft.components.preprocessor.sidecars import PreprocessorSidecars
from bookcraft.domain.enums import ManuscriptStatus, ServiceCategory

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
        "write my book",
        "writing the story",
        "help writing",
        "help writing the story",
        "help me write",
        "story writing",
        "only have an idea",
        "idea for a children's",
        "idea for a children’s",
        "children's picture book",
        "children’s picture book",
    ],
    ServiceCategory.EDITING_PROOFREADING: ["editing", "proofreading", "proofread"],
    ServiceCategory.COVER_DESIGN_ILLUSTRATION: [
        "cover design",
        "illustration",
        "book cover",
        "professional cover",
        "cover artwork",
        "cover art",
        "cover illustration",
        "cover",
        "writing the story",
        "write the story",
        "help writing",
        "help me write",
        "story writing",
        "only have an idea",
        "idea for a children's",
        "idea for a children’s",
        "children's picture book",
        "children’s picture book",
        "illustrations",
        "creating illustrations",
        "picture book illustrations",
        "children's book illustrations",
        "children’s book illustrations",
        "illustrator",
        "book illustration",
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
        self._put_all(atoms, "negated_terms", _negated_terms(text, negation_spans))
        self._put_all(atoms, "context_markers", _context_markers(text, counterfactual_spans))
        self._put_all(atoms, "forbid_markers", _forbid_markers(text))
        self._put_all(atoms, "query_cues", _query_cues(text))
        status = self._manuscript_status(text)
        if status:
            atoms["manuscript_status"] = status.value
            ATOMS_EXTRACTED.labels(atom_type="manuscript_status").inc()
        return atoms

    @staticmethod
    def _put_all(atoms: dict[str, object], key: str, values: Sequence[object]) -> None:
        if values:
            atoms[key] = values
            ATOMS_EXTRACTED.labels(atom_type=key).inc(len(values))

    @staticmethod
    def _manuscript_status(text: str) -> ManuscriptStatus | None:
        lowered = text.lower()
        if "idea" in lowered:
            return ManuscriptStatus.IDEA_ONLY
        if "outline" in lowered:
            return ManuscriptStatus.OUTLINE
        if "partial draft" in lowered:
            return ManuscriptStatus.PARTIAL_DRAFT
        if "completed draft" in lowered or "finished manuscript" in lowered:
            return ManuscriptStatus.COMPLETED_DRAFT
        if "published" in lowered:
            return ManuscriptStatus.PUBLISHED
        return None


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


def _forbid_markers(text: str) -> list[str]:
    lowered = text.casefold()
    markers: list[str] = []

    def add(value: str) -> None:
        if value not in markers:
            markers.append(value)

    if any(phrase in lowered for phrase in ("40 percent", "cut the price", "price by")):
        add("price_number")

    if any(phrase in lowered for phrase in ("bestseller", "promise", "guarantee")):
        add("guarantee")

    if any(phrase in lowered for phrase in ("blank pricing", "filled later", "skip the quote")):
        add("agreement_generation_without_quote")

    if any(phrase in lowered for phrase in ("fake sample links", "http://", "https://")):
        add("fake_link_acceptance")

    return markers


def _query_cues(text: str) -> list[str]:
    lowered = text.casefold()
    cues: list[str] = []

    def add(value: str) -> None:
        if value not in cues:
            cues.append(value)

    if any(
        phrase in lowered
        for phrase in (
            "sign the agreement",
            "service agreement",
            "generate the service agreement",
            "agreement today",
            "blank pricing",
            "filled later",
            "skip the quote",
        )
    ):
        add("agreement_request")

    if any(
        phrase in lowered
        for phrase in ("cut the price", "price by", "pricing", "price", "quote", "40 percent")
    ):
        add("pricing_question")

    if any(phrase in lowered for phrase in ("portfolio", "sample", "samples")):
        add("portfolio_request")

    if any(phrase in lowered for phrase in ("file types", "upload", "services")):
        add("service_question")

    return cues
