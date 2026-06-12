from __future__ import annotations

import json
import re
from pathlib import Path

from .schemas import CompiledRulePack, RulePack, TriMatchLayer, TriMatchRule


class RuleRepository:
    def __init__(self, rule_dir: str | Path) -> None:
        self.rule_dir = Path(rule_dir)

    def load_active_rules(self) -> RulePack:
        rules: list[TriMatchRule] = []
        versions: list[str] = []
        for path in sorted(self.rule_dir.glob("*.json")):
            loaded = json.loads(path.read_text(encoding="utf-8"))
            pack = RulePack.model_validate(loaded)
            versions.append(pack.version)
            rules.extend(rule for rule in pack.rules if rule.enabled)
        return RulePack(version="+".join(versions) or "empty", rules=rules)

    def build_compiled_pack(self, rule_pack: RulePack | None = None) -> CompiledRulePack:
        """Build a CompiledRulePack with pre-compiled indexes.

        If rule_pack is None, loads active rules first.
        """
        if rule_pack is None:
            rule_pack = self.load_active_rules()

        compiled = CompiledRulePack(rule_pack=rule_pack)

        exact_phrase_parts: list[str] = []
        exact_rule_by_id: dict[str, TriMatchRule] = {}

        compiled_regex: dict[str, re.Pattern[str]] = {}
        pattern_first_token_index: dict[str, list[str]] = {}

        for rule in rule_pack.rules:
            if not rule.enabled:
                continue

            if rule.layer == TriMatchLayer.EXACT and rule.phrases:
                for phrase in rule.phrases:
                    exact_phrase_parts.append(re.escape(phrase.lower()))
                exact_rule_by_id[rule.id] = rule

            elif rule.layer == TriMatchLayer.REGEX and rule.regex:
                try:
                    compiled_regex[rule.id] = re.compile(rule.regex, re.IGNORECASE)
                except re.error:
                    pass  # Skip malformed patterns

            elif rule.layer == TriMatchLayer.PATTERN and rule.phrases:
                for phrase in rule.phrases:
                    first_token = phrase.lower().split()[0] if phrase.split() else ""
                    if first_token:
                        pattern_first_token_index.setdefault(first_token, []).append(rule.id)

        # Build EXACT union pattern (case-insensitive, word-boundary anchored)
        if exact_phrase_parts:
            union = "|".join(f"\\b(?:{p})\\b" for p in exact_phrase_parts)
            try:
                compiled.exact_union_pattern = re.compile(union, re.IGNORECASE)
            except re.error:
                compiled.exact_union_pattern = None

        compiled.exact_rule_by_id = exact_rule_by_id
        compiled.compiled_regex = compiled_regex
        compiled.pattern_first_token_index = pattern_first_token_index

        return compiled

    async def precompute_semantic_embeddings(
        self,
        compiled_pack: CompiledRulePack,
        tei_url: str,
        tei_timeout: float = 10.0,
    ) -> CompiledRulePack:
        """Fetch TEI embeddings for all semantic-layer rule phrases and store in pack."""
        import httpx

        semantic_rules = [
            rule
            for rule in compiled_pack.rule_pack.rules
            if rule.enabled and rule.layer == TriMatchLayer.SEMANTIC and rule.phrases
        ]
        if not semantic_rules:
            return compiled_pack

        # Collect all phrases across all semantic rules
        all_phrases: list[str] = []
        rule_phrase_spans: list[tuple[str, int, int]] = []  # (rule_id, start, end)
        for rule in semantic_rules:
            start = len(all_phrases)
            all_phrases.extend(rule.phrases)
            rule_phrase_spans.append((rule.id, start, len(all_phrases)))

        # Call TEI batch embed endpoint
        try:
            async with httpx.AsyncClient(timeout=tei_timeout) as client:
                resp = await client.post(
                    f"{tei_url.rstrip('/')}/embed",
                    json={"inputs": all_phrases},
                )
                resp.raise_for_status()
                all_embeddings: list[list[float]] = resp.json()
        except Exception:
            return compiled_pack  # degrade gracefully

        # Average embeddings per rule (centroid of all its phrases)
        import math

        embeddings: list[tuple[str, list[float]]] = []
        rule_index: dict[str, int] = {}

        for rule_id, start, end in rule_phrase_spans:
            phrase_embs = all_embeddings[start:end]
            if not phrase_embs:
                continue
            dim = len(phrase_embs[0])
            centroid = [sum(e[i] for e in phrase_embs) / len(phrase_embs) for i in range(dim)]
            # L2-normalize
            norm = math.sqrt(sum(x * x for x in centroid)) or 1.0
            centroid = [x / norm for x in centroid]
            rule_index[rule_id] = len(embeddings)
            embeddings.append((rule_id, centroid))

        compiled_pack.semantic_embeddings = embeddings
        compiled_pack.semantic_rule_index = rule_index
        return compiled_pack
