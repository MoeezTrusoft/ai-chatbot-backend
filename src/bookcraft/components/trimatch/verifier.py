from __future__ import annotations

import json
import re
from pathlib import Path

from prometheus_client import Counter

from bookcraft.components.preprocessor.schemas import ProcessedMessage

from .engine import TRIMATCH_PRECISION, TRIMATCH_RECALL, TriMatchEngine
from .schemas import (
    EvalExample,
    RulePack,
    TriMatchDimension,
    TriMatchLayer,
    TriMatchMode,
    TriMatchVerificationResult,
)

RECALL_FLOORS = {
    TriMatchLayer.EXACT: 0.20,
    TriMatchLayer.REGEX: 0.35,
    TriMatchLayer.PATTERN: 0.45,
}
TRIMATCH_VERIFIER_FAILURES = Counter(
    "trimatch_verifier_failures_total",
    "Tri-Match verifier failures by reason.",
    ["reason"],
)


class TriMatchVerifier:
    def verify(
        self,
        rule_pack: RulePack,
        eval_examples: list[EvalExample],
    ) -> TriMatchVerificationResult:
        errors: list[str] = []
        warnings: list[str] = []
        for rule in rule_pack.rules:
            if rule.regex:
                try:
                    re.compile(rule.regex)
                except re.error as exc:
                    errors.append(f"{rule.id}: invalid regex: {exc}")
            if rule.target.dimension == TriMatchDimension.FUNNEL_STAGE:
                forbidden = " ".join([*rule.phrases, rule.regex or "", *rule.pattern]).casefold()
                forbidden_terms = ["price", "cost", "contract text", "legal clause"]
                if any(term in forbidden for term in forbidden_terms):
                    errors.append(
                        f"{rule.id}: funnel-stage rules cannot encode pricing/legal decisions"
                    )
            shortcut_forbidden = rule.layer in {TriMatchLayer.SEMANTIC, TriMatchLayer.FUZZY}
            if shortcut_forbidden and rule.shortcut_allowed:
                errors.append(f"{rule.id}: semantic/fuzzy shortcut is forbidden")

        precision, recall = evaluate_rule_pack(rule_pack, eval_examples)
        for key, value in precision.items():
            dimension, layer = key.split(":", 1)
            TRIMATCH_PRECISION.labels(dimension=dimension, layer=layer).set(value)
            if layer in {"exact", "regex", "pattern"} and value < 0.97:
                errors.append(f"{key}: shortcut precision below 0.97")
                TRIMATCH_VERIFIER_FAILURES.labels(reason="precision_floor").inc()
        for key, value in recall.items():
            dimension, layer = key.split(":", 1)
            TRIMATCH_RECALL.labels(dimension=dimension, layer=layer).set(value)
            layer_enum = TriMatchLayer(layer)
            if layer_enum in RECALL_FLOORS and value < RECALL_FLOORS[layer_enum]:
                errors.append(f"{key}: recall below floor {RECALL_FLOORS[layer_enum]}")
                TRIMATCH_VERIFIER_FAILURES.labels(reason="recall_floor").inc()

        return TriMatchVerificationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            precision=precision,
            recall=recall,
        )


def load_eval_examples(eval_dir: str | Path) -> list[EvalExample]:
    examples: list[EvalExample] = []
    for path in sorted(Path(eval_dir).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                examples.append(EvalExample.model_validate(json.loads(line)))
    return examples


def evaluate_rule_pack(
    rule_pack: RulePack,
    eval_examples: list[EvalExample],
) -> tuple[dict[str, float], dict[str, float]]:
    precision_counts: dict[str, list[int]] = {}
    recall_counts: dict[str, list[int]] = {}
    engine = TriMatchEngine(
        rule_pack=rule_pack,
        mode=TriMatchMode.SHADOW,
        shortcut_layers={TriMatchLayer.EXACT, TriMatchLayer.REGEX, TriMatchLayer.PATTERN},
    )
    for example in eval_examples:
        processed = _processed(example.text)
        result = engine.classify(processed)
        dimension_value = getattr(result, _result_attr(example.dimension))
        expected_matched = str(dimension_value) == example.expected
        matched_layers = {
            evidence.layer
            for evidence in result.evidence
            if evidence.dimension == example.dimension
        }
        for evidence in result.evidence:
            if evidence.dimension != example.dimension:
                continue
            key = f"{evidence.dimension.value}:{evidence.layer.value}"
            precision_counts.setdefault(key, [0, 0])
            precision_counts[key][1] += 1
            if evidence.target == example.expected:
                precision_counts[key][0] += 1
        for layer in [TriMatchLayer.EXACT, TriMatchLayer.REGEX, TriMatchLayer.PATTERN]:
            key = f"{example.dimension.value}:{layer.value}"
            recall_counts.setdefault(key, [0, 0])
            recall_counts[key][1] += 1
            if expected_matched and layer in matched_layers:
                recall_counts[key][0] += 1
    precision = {
        key: correct / total if total else 0.0 for key, (correct, total) in precision_counts.items()
    }
    recall = {
        key: correct / total if total else 0.0 for key, (correct, total) in recall_counts.items()
    }
    return precision, recall


def _processed(text: str) -> ProcessedMessage:
    tokens = []
    start = 0
    from bookcraft.components.preprocessor.schemas import TokenInfo

    for word in re.findall(r"\b[\w']+\b", text):
        index = text.casefold().find(word.casefold(), start)
        tokens.append(
            TokenInfo(text=word, lemma=word.casefold(), start=index, end=index + len(word))
        )
        start = index + len(word)
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=tokens,
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[1.0],
        language="en",
        char_count=len(text),
    )


def _result_attr(dimension: TriMatchDimension) -> str:
    if dimension == TriMatchDimension.QUERY_INTENT:
        return "query_primary"
    if dimension == TriMatchDimension.SERVICE_INTENT:
        return "service_primary"
    return "funnel_stage"
