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
    ServiceCategory.GHOSTWRITING: ["ghostwriting", "ghost writer", "write my book"],
    ServiceCategory.EDITING_PROOFREADING: ["editing", "proofreading", "proofread"],
    ServiceCategory.COVER_DESIGN_ILLUSTRATION: ["cover design", "illustration", "book cover"],
    ServiceCategory.INTERIOR_FORMATTING: ["interior formatting", "formatting", "typesetting"],
    ServiceCategory.AUDIOBOOK_PRODUCTION: ["audiobook", "audio book", "narration"],
    ServiceCategory.PUBLISHING_DISTRIBUTION: [
        "publishing",
        "distribution",
        "amazon",
        "kdp",
        "ingramspark",
    ],
    ServiceCategory.MARKETING_PROMOTION: ["marketing", "promotion", "ads"],
    ServiceCategory.AUTHOR_WEBSITE: ["author website", "website"],
    ServiceCategory.VIDEO_TRAILER: ["video trailer", "trailer"],
}

CUE_SPAN_WINDOW_CHARS = 80
CUE_TERMINATOR_RE = re.compile(
    r"[,.!?;]|\b(?:but|however|instead|rather|except|unless|although)\b",
    flags=re.IGNORECASE,
)
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
                end = _cue_span_end(text, match.end())
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
