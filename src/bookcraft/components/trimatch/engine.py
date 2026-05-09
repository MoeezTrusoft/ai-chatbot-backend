from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram

from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span
from bookcraft.components.trg import TRGContext
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory

from .schemas import (
    RulePack,
    TriMatchDimension,
    TriMatchEvidence,
    TriMatchLayer,
    TriMatchMode,
    TriMatchResult,
    TriMatchRule,
)

TRIMATCH_VOTES = Counter(
    "trimatch_votes_total",
    "Tri-Match votes emitted.",
    ["dimension", "layer", "result"],
)
TRIMATCH_LATENCY = Histogram(
    "trimatch_match_latency_seconds",
    "Tri-Match matcher latency.",
    ["dimension", "layer"],
)
TRIMATCH_PRECISION = Gauge(
    "trimatch_precision",
    "Tri-Match measured precision.",
    ["dimension", "layer"],
)
TRIMATCH_RECALL = Gauge(
    "trimatch_recall",
    "Tri-Match measured recall.",
    ["dimension", "layer"],
)
TRIMATCH_SHORTCUTS = Counter(
    "trimatch_shortcuts_total",
    "Tri-Match shortcut decisions.",
    ["dimension", "layer", "status"],
)
FUNNEL_SIGNAL_VOTES = Counter(
    "funnel_signal_votes_total",
    "Shadow funnel-stage votes emitted by Tri-Match under D-081.",
    ["stage", "mode"],
)

LAYER_WEIGHTS = {
    TriMatchLayer.EXACT: 1.0,
    TriMatchLayer.REGEX: 0.9,
    TriMatchLayer.PATTERN: 0.8,
    TriMatchLayer.SEMANTIC: 0.6,
    TriMatchLayer.FUZZY: 0.0,
}


@dataclass(slots=True)
class TriMatchEngine:
    rule_pack: RulePack
    mode: TriMatchMode = TriMatchMode.SHADOW
    shortcut_layers: set[TriMatchLayer] | None = None
    shortcut_threshold: float = 0.97
    funnel_stage_weight: float = 0.0
    fuzzy_enabled: bool = False

    def classify(
        self,
        processed_message: ProcessedMessage,
        trg_context: TRGContext | None = None,
    ) -> TriMatchResult:
        del trg_context
        evidence = self._collect_evidence(processed_message)
        scores = aggregate_scores(evidence)
        query_primary = _best_enum(scores[TriMatchDimension.QUERY_INTENT], QueryIntentType)
        service_primary = _best_enum(scores[TriMatchDimension.SERVICE_INTENT], ServiceCategory)
        funnel_stage = _best_enum(scores[TriMatchDimension.FUNNEL_STAGE], SalesStage)
        confidence = max((item.confidence for item in evidence), default=0.0)
        shortcut_eligible = self._shortcut_eligible(evidence)
        if funnel_stage is not None and self.funnel_stage_weight == 0:
            shadow_only = [TriMatchDimension.FUNNEL_STAGE]
            FUNNEL_SIGNAL_VOTES.labels(stage=funnel_stage.value, mode="shadow").inc()
        else:
            shadow_only = []
        return TriMatchResult(
            query_primary=query_primary,
            service_primary=service_primary,
            funnel_stage=funnel_stage,
            confidence=confidence,
            evidence=evidence,
            mode=self.mode,
            shadow_only_dimensions=shadow_only,
            shortcut_eligible=shortcut_eligible,
        )

    def _collect_evidence(self, message: ProcessedMessage) -> list[TriMatchEvidence]:
        evidence: list[TriMatchEvidence] = []
        for rule in self.rule_pack.rules:
            if rule.layer == TriMatchLayer.FUZZY and not self.fuzzy_enabled:
                continue
            with TRIMATCH_LATENCY.labels(
                dimension=rule.target.dimension.value,
                layer=rule.layer.value,
            ).time():
                matched = match_rule(rule, message)
            if matched is None:
                TRIMATCH_VOTES.labels(
                    dimension=rule.target.dimension.value,
                    layer=rule.layer.value,
                    result="miss",
                ).inc()
                continue
            damped_confidence, flags = dampen_confidence(matched, rule.confidence, message)
            if damped_confidence <= 0:
                TRIMATCH_VOTES.labels(
                    dimension=rule.target.dimension.value,
                    layer=rule.layer.value,
                    result="damped",
                ).inc()
                continue
            shortcut_eligible = (
                self.mode == TriMatchMode.SHORTCUT_ENABLED
                and rule.shortcut_allowed
                and rule.layer in (self.shortcut_layers or set())
                and rule.layer not in {TriMatchLayer.SEMANTIC, TriMatchLayer.FUZZY}
                and damped_confidence >= self.shortcut_threshold
            )
            evidence.append(
                TriMatchEvidence(
                    rule_id=rule.id,
                    dimension=rule.target.dimension,
                    target=rule.target.value,
                    layer=rule.layer,
                    matched_text=matched,
                    confidence=damped_confidence,
                    shortcut_eligible=shortcut_eligible,
                    **flags,
                )
            )
            TRIMATCH_VOTES.labels(
                dimension=rule.target.dimension.value,
                layer=rule.layer.value,
                result="hit",
            ).inc()
        return evidence

    def _shortcut_eligible(self, evidence: list[TriMatchEvidence]) -> bool:
        if self.mode != TriMatchMode.SHORTCUT_ENABLED:
            return False
        eligible = any(item.shortcut_eligible for item in evidence)
        for item in evidence:
            if item.shortcut_eligible:
                TRIMATCH_SHORTCUTS.labels(
                    dimension=item.dimension.value,
                    layer=item.layer.value,
                    status="eligible",
                ).inc()
        return eligible


def match_rule(rule: TriMatchRule, message: ProcessedMessage) -> str | None:
    text = message.normalized.casefold()
    if rule.layer == TriMatchLayer.EXACT:
        for phrase in rule.phrases:
            normalized_phrase = phrase.casefold()
            if text == normalized_phrase or re.search(
                rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)",
                text,
            ):
                return phrase
        return None
    if rule.layer == TriMatchLayer.REGEX:
        if rule.regex is None:
            return None
        match = re.search(rule.regex, message.normalized, flags=re.IGNORECASE)
        return match.group(0) if match else None
    if rule.layer == TriMatchLayer.PATTERN:
        token_lemmas = [token.lemma.casefold() for token in message.tokens]
        pattern = [part.casefold() for part in rule.pattern]
        if contains_subsequence(token_lemmas, pattern):
            return " ".join(rule.pattern)
        return None
    if rule.layer == TriMatchLayer.SEMANTIC:
        return semantic_match(rule, message)
    return None


def contains_subsequence(tokens: list[str], pattern: list[str]) -> bool:
    if not pattern:
        return False
    for index in range(0, len(tokens) - len(pattern) + 1):
        if tokens[index : index + len(pattern)] == pattern:
            return True
    return False


def semantic_match(rule: TriMatchRule, message: ProcessedMessage) -> str | None:
    if not message.embedding:
        return None
    text_terms = set(re.findall(r"\w+", message.normalized.casefold()))
    best_score = 0.0
    best_example: str | None = None
    for example in rule.semantic_examples:
        example_terms = set(re.findall(r"\w+", example.casefold()))
        overlap = len(text_terms & example_terms)
        denominator = math.sqrt(max(1, len(text_terms)) * max(1, len(example_terms)))
        score = overlap / denominator
        if score > best_score:
            best_score = score
            best_example = example
    return best_example if best_score >= 0.5 else None


def dampen_confidence(
    matched_text: str,
    confidence: float,
    message: ProcessedMessage,
) -> tuple[float, dict[str, bool]]:
    start = message.normalized.casefold().find(matched_text.casefold())
    end = start + len(matched_text) if start >= 0 else start
    negated = span_overlaps(start, end, message.negation_spans)
    hedged = span_overlaps(start, end, message.hedge_spans)
    counterfactual = span_overlaps(start, end, message.counterfactual_spans)
    if negated or counterfactual:
        confidence = 0.0
    elif hedged:
        confidence *= 0.4
    return confidence, {
        "negated": negated,
        "hedged": hedged,
        "counterfactual": counterfactual,
    }


def span_overlaps(start: int, end: int, spans: list[Span]) -> bool:
    if start < 0:
        return False
    return any(start < span.end and end > span.start for span in spans)


def aggregate_scores(
    evidence: list[TriMatchEvidence],
) -> dict[TriMatchDimension, dict[str, float]]:
    scores: dict[TriMatchDimension, dict[str, float]] = defaultdict(dict)
    for item in evidence:
        current = scores[item.dimension].get(item.target, 0.0)
        scores[item.dimension][item.target] = current + item.confidence * LAYER_WEIGHTS[item.layer]
    return scores


def _best_enum(
    scores: dict[str, float],
    enum_type: type[QueryIntentType] | type[ServiceCategory] | type[SalesStage],
) -> QueryIntentType | ServiceCategory | SalesStage | None:
    if not scores:
        return None
    value = max(scores.items(), key=lambda item: item[1])[0]
    return enum_type(value)
