from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram

from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span
from bookcraft.components.trg import TRGContext
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory

from .context_arbitration import apply_context_arbitration
from .schemas import (
    CompiledRulePack,
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
    compiled_pack: CompiledRulePack | None = None

    def classify(
        self,
        processed_message: ProcessedMessage,
        trg_context: TRGContext | None = None,
    ) -> TriMatchResult:
        raw_evidence = self._collect_evidence(processed_message)
        if trg_context is not None:
            raw_evidence, _trg_audit = _apply_trg_arbitration(raw_evidence, trg_context)
        evidence = apply_context_arbitration(raw_evidence, processed_message)
        scores = aggregate_scores(evidence)
        query_primary = _best_enum(scores[TriMatchDimension.QUERY_INTENT], QueryIntentType)
        service_primary = _best_enum(scores[TriMatchDimension.SERVICE_INTENT], ServiceCategory)
        service_primary, service_secondary = _ordered_services_from_atoms(
            processed_message,
            scores[TriMatchDimension.SERVICE_INTENT],
            service_primary,
        )
        funnel_stage = _best_enum(scores[TriMatchDimension.FUNNEL_STAGE], SalesStage)
        service_scores = scores[TriMatchDimension.SERVICE_INTENT]
        if service_primary is not None and service_scores:
            winning_score = service_scores.get(service_primary.value, 0.0)
            total_score = sum(service_scores.values())
            confidence = winning_score / total_score if total_score > 0 else 0.0
        else:
            query_scores = scores[TriMatchDimension.QUERY_INTENT]
            if query_primary is not None and query_scores:
                winning_score = query_scores.get(query_primary.value, 0.0)
                total_score = sum(query_scores.values())
                confidence = winning_score / total_score if total_score > 0 else 0.0
            else:
                confidence = max((item.confidence for item in evidence), default=0.0)
        shortcut_eligible = self._shortcut_eligible(evidence)
        if funnel_stage is not None and (
            self.mode == TriMatchMode.SHADOW or self.funnel_stage_weight == 0
        ):
            shadow_only = [TriMatchDimension.FUNNEL_STAGE]
            FUNNEL_SIGNAL_VOTES.labels(stage=funnel_stage.value, mode="shadow").inc()
        else:
            shadow_only = []
        return TriMatchResult(
            query_primary=query_primary,
            service_primary=service_primary,
            service_secondary=service_secondary,
            funnel_stage=funnel_stage,
            confidence=confidence,
            evidence=evidence,
            mode=self.mode,
            shadow_only_dimensions=shadow_only,
            shortcut_eligible=shortcut_eligible,
        )

    def _collect_evidence(self, message: ProcessedMessage) -> list[TriMatchEvidence]:
        evidence: list[TriMatchEvidence] = []

        # EXACT layer fast pre-screen: if the compiled union pattern doesn't match,
        # no EXACT rule can fire — skip all EXACT rules for this message.
        exact_prescreened = (
            self.compiled_pack is not None
            and self.compiled_pack.exact_union_pattern is not None
            and not self.compiled_pack.exact_union_pattern.search(message.normalized)
        )

        # SEMANTIC compiled path: when pre-computed embeddings are available, collect
        # all SEMANTIC rules and run them via cosine similarity instead of match_rule.
        use_semantic_compiled = (
            self.compiled_pack is not None
            and bool(self.compiled_pack.semantic_embeddings)
            and bool(message.embedding)
        )
        if use_semantic_compiled:
            semantic_rules = [
                rule
                for rule in self.rule_pack.rules
                if rule.layer == TriMatchLayer.SEMANTIC
            ]
            for sem_ev in self._match_semantic_compiled(message, semantic_rules):
                evidence.append(sem_ev)
                TRIMATCH_VOTES.labels(
                    dimension=sem_ev.dimension.value,
                    layer=sem_ev.layer.value,
                    result="hit",
                ).inc()

        for rule in self.rule_pack.rules:
            if rule.layer == TriMatchLayer.FUZZY and not self.fuzzy_enabled:
                continue

            # Skip EXACT rules when union pre-screen ruled out any match
            if rule.layer == TriMatchLayer.EXACT and exact_prescreened:
                TRIMATCH_VOTES.labels(
                    dimension=rule.target.dimension.value,
                    layer=rule.layer.value,
                    result="miss",
                ).inc()
                continue

            # Skip SEMANTIC rules when the compiled path already handled them
            if rule.layer == TriMatchLayer.SEMANTIC and use_semantic_compiled:
                continue

            with TRIMATCH_LATENCY.labels(
                dimension=rule.target.dimension.value,
                layer=rule.layer.value,
            ).time():
                # Use pre-compiled regex pattern when available
                if (
                    rule.layer == TriMatchLayer.REGEX
                    and self.compiled_pack is not None
                    and rule.id in self.compiled_pack.compiled_regex
                ):
                    matched = _match_rule_with_compiled_regex(
                        rule, message, self.compiled_pack.compiled_regex[rule.id]
                    )
                else:
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

    def _match_semantic_compiled(
        self,
        processed_message: ProcessedMessage,
        rules: list[TriMatchRule],
    ) -> list[TriMatchEvidence]:
        """Use pre-computed embeddings for SEMANTIC matching via cosine similarity."""
        if (
            self.compiled_pack is None
            or not self.compiled_pack.semantic_embeddings
            or not processed_message.embedding
        ):
            return []

        query_emb = processed_message.embedding
        query_norm = math.sqrt(sum(x * x for x in query_emb)) or 1.0
        query_unit = [x / query_norm for x in query_emb]

        evidence: list[TriMatchEvidence] = []
        for rule in rules:
            if rule.layer != TriMatchLayer.SEMANTIC:
                continue
            idx = self.compiled_pack.semantic_rule_index.get(rule.id)
            if idx is None:
                continue
            _, rule_emb = self.compiled_pack.semantic_embeddings[idx]
            cosine = sum(a * b for a, b in zip(query_unit, rule_emb))
            if cosine >= 0.6:  # threshold: ~36° angle
                damped_confidence, flags = dampen_confidence(
                    processed_message.normalized[:100],
                    rule.confidence * cosine,
                    processed_message,
                )
                if damped_confidence <= 0:
                    TRIMATCH_VOTES.labels(
                        dimension=rule.target.dimension.value,
                        layer=rule.layer.value,
                        result="damped",
                    ).inc()
                    continue
                evidence.append(
                    TriMatchEvidence(
                        rule_id=rule.id,
                        dimension=rule.target.dimension,
                        target=rule.target.value,
                        layer=TriMatchLayer.SEMANTIC,
                        matched_text=processed_message.normalized[:100],
                        confidence=damped_confidence,
                        shortcut_eligible=False,
                        **flags,
                    )
                )
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


def _apply_trg_arbitration(
    evidence: list[TriMatchEvidence],
    trg_context: TRGContext,
) -> tuple[list[TriMatchEvidence], list[str]]:
    """Conservative TRG-aware evidence suppression. Suppression-only, never boosts.

    Returns (filtered_evidence, audit_strings).
    """
    if not trg_context:
        return evidence, []

    audit: list[str] = []
    filtered: list[TriMatchEvidence] = []

    # Build set of declined/confirmed services from TRG service shifts
    declined_services: set[str] = set()
    for shift in trg_context.service_shifts:
        # A shift away from a service means the user changed their mind
        # Mark previously-active services as context-declined if there was an explicit switch
        if shift.mode == "switch" and shift.previous_service:
            declined_services.add(shift.previous_service.lower())

    # Build set of forbidden re-ask targets from TRG
    forbidden_lower = {f.lower() for f in trg_context.forbidden_reasks}

    for item in evidence:
        # Suppress SERVICE_INTENT evidence for services the user explicitly switched away from
        if (
            item.dimension == TriMatchDimension.SERVICE_INTENT
            and item.target.lower() in declined_services
        ):
            audit.append(f"trg_suppressed:declined_service:{item.target}:{item.rule_id}")
            continue

        # Suppress evidence that re-fires on a topic in forbidden_reasks
        # (e.g., re-asking genre when it's already captured)
        if any(item.target.lower() in f or f in item.target.lower() for f in forbidden_lower):
            audit.append(f"trg_suppressed:forbidden_reask:{item.target}:{item.rule_id}")
            continue

        filtered.append(item)

    return filtered, audit


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


def _match_rule_with_compiled_regex(
    rule: TriMatchRule,
    message: ProcessedMessage,
    compiled_pattern: re.Pattern[str],
) -> str | None:
    """Match a REGEX rule using a pre-compiled pattern object."""
    match = compiled_pattern.search(message.normalized)
    return match.group(0) if match else None


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


def _ordered_services_from_atoms(
    message: ProcessedMessage,
    scores: dict[str, float],
    primary: QueryIntentType | ServiceCategory | SalesStage | None,
) -> tuple[ServiceCategory | None, list[ServiceCategory]]:
    atom_services = message.deterministic_atoms.get("services")
    ordered: list[ServiceCategory] = []

    if isinstance(atom_services, list):
        for item in atom_services:
            if not isinstance(item, str):
                continue
            try:
                atom_service = ServiceCategory(item)
            except ValueError:
                continue
            if atom_service.value not in scores:
                continue
            if atom_service not in ordered:
                ordered.append(atom_service)

    fallback_services: list[QueryIntentType | ServiceCategory | SalesStage | None] = [
        primary,
        *_secondary_service_enums(scores, primary),
    ]

    for candidate in fallback_services:
        if isinstance(candidate, ServiceCategory) and candidate not in ordered:
            ordered.append(candidate)

    if not ordered:
        return None, []

    return ordered[0], ordered[1:]


def _secondary_service_enums(
    scores: dict[str, float],
    primary: QueryIntentType | ServiceCategory | SalesStage | None,
) -> list[ServiceCategory]:
    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    secondary: list[ServiceCategory] = []

    for value, score in ranked:
        if score <= 0:
            continue

        service = ServiceCategory(value)
        if service == primary:
            continue

        secondary.append(service)

    return secondary


def _best_enum(
    scores: dict[str, float],
    enum_type: type[QueryIntentType] | type[ServiceCategory] | type[SalesStage],
) -> QueryIntentType | ServiceCategory | SalesStage | None:
    if not scores:
        return None
    value = max(scores.items(), key=lambda item: item[1])[0]
    return enum_type(value)
