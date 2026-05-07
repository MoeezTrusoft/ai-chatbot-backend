import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

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
    ServiceCategory.INTERIOR_FORMATTING: ["formatting", "interior formatting", "typesetting"],
    ServiceCategory.AUDIOBOOK_PRODUCTION: ["audiobook", "audio book", "narration"],
    ServiceCategory.PUBLISHING_DISTRIBUTION: ["publishing", "distribution", "amazon"],
    ServiceCategory.MARKETING_PROMOTION: ["marketing", "promotion", "ads"],
    ServiceCategory.AUTHOR_WEBSITE: ["author website", "website"],
    ServiceCategory.VIDEO_TRAILER: ["video trailer", "trailer"],
}


@dataclass(slots=True)
class SharedPreprocessor:
    sidecars: PreprocessorSidecars
    embedding_client: EmbeddingClient

    async def process(self, raw: str, language: str = "en") -> ProcessedMessage:
        with PREPROCESSOR_SECONDS.time():
            normalized = self._normalize(raw)
            tokens = self._tokenize(normalized)
            negation_spans = self._cue_spans(normalized, self.sidecars.negation_cues)
            hedge_spans = self._cue_spans(normalized, self.sidecars.hedge_cues)
            counterfactual_spans = self._cue_spans(normalized, self.sidecars.counterfactual_cues)
            tokens = self._mark_tokens(tokens, negation_spans, hedge_spans, counterfactual_spans)
            atoms = self._deterministic_atoms(normalized)
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

    @staticmethod
    def _cue_spans(text: str, cues: list[str]) -> list[Span]:
        spans: list[Span] = []
        for cue in cues:
            for match in re.finditer(rf"\b{re.escape(cue)}\b", text, flags=re.IGNORECASE):
                end = min(len(text), match.end() + 80)
                spans.append(
                    Span(start=match.start(), end=end, text=text[match.start() : end], cue=cue)
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

    def _deterministic_atoms(self, text: str) -> dict[str, object]:
        atoms: dict[str, object] = {}
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
        services = [
            service.value
            for service, keywords in SERVICE_KEYWORDS.items()
            if any(keyword in text.lower() for keyword in keywords)
        ]
        self._put_all(atoms, "services", services)
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
