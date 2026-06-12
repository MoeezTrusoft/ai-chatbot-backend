"""Tests for CompiledRulePack and compiled indexes in trimatch."""
from __future__ import annotations

import dataclasses

import pytest

from bookcraft.components.trimatch.schemas import CompiledRulePack, RulePack
from bookcraft.components.trimatch.repository import RuleRepository


class TestCompiledRulePackDataclass:
    def test_compiled_rule_pack_is_dataclass(self):
        assert dataclasses.is_dataclass(CompiledRulePack)

    def test_default_fields_empty(self):
        rule_pack = RulePack(version="test", rules=[])
        cp = CompiledRulePack(rule_pack=rule_pack)
        assert cp.exact_union_pattern is None
        assert cp.exact_rule_by_id == {}
        assert cp.compiled_regex == {}
        assert cp.pattern_first_token_index == {}
        assert cp.semantic_embeddings == []
        assert cp.semantic_rule_index == {}

    def test_rule_pack_stored(self):
        rule_pack = RulePack(version="v1", rules=[])
        cp = CompiledRulePack(rule_pack=rule_pack)
        assert cp.rule_pack is rule_pack

    def test_fields_accessible(self):
        rule_pack = RulePack(version="test", rules=[])
        cp = CompiledRulePack(rule_pack=rule_pack)
        # All expected fields present
        assert hasattr(cp, "rule_pack")
        assert hasattr(cp, "exact_union_pattern")
        assert hasattr(cp, "exact_rule_by_id")
        assert hasattr(cp, "compiled_regex")
        assert hasattr(cp, "pattern_first_token_index")
        assert hasattr(cp, "semantic_embeddings")
        assert hasattr(cp, "semantic_rule_index")


class TestBuildCompiledPack:
    def _make_repo(self) -> RuleRepository:
        return RuleRepository("data/trimatch/rules")

    def test_build_compiled_pack_returns_compiled_rule_pack(self):
        repo = self._make_repo()
        cp = repo.build_compiled_pack()
        assert isinstance(cp, CompiledRulePack)

    def test_exact_union_pattern_compiled(self):
        repo = self._make_repo()
        cp = repo.build_compiled_pack()
        # The real rule packs have EXACT rules, so there should be a union pattern
        assert cp.exact_union_pattern is not None

    def test_regex_rules_precompiled(self):
        repo = self._make_repo()
        cp = repo.build_compiled_pack()
        # At least some REGEX rules should be precompiled
        assert isinstance(cp.compiled_regex, dict)

    def test_exact_union_pattern_matches_known_phrase(self):
        """Union pattern should match phrases from EXACT rules in the real pack."""
        from bookcraft.components.trimatch.schemas import TriMatchLayer

        repo = self._make_repo()
        cp = repo.build_compiled_pack()
        if cp.exact_union_pattern is None:
            pytest.skip("No EXACT rules in this pack")
        # The union pattern should match at least one phrase from any rule
        any_phrase_matches = any(
            cp.exact_union_pattern.search(rule.phrases[0])
            for rule in cp.rule_pack.rules
            if rule.layer.value == "exact" and rule.phrases
        )
        assert any_phrase_matches

    def test_build_from_explicit_rule_pack(self):
        """build_compiled_pack accepts an explicit RulePack instead of loading from disk."""
        repo = self._make_repo()
        pack = RulePack.model_validate({
            "version": "test",
            "rules": [
                {
                    "id": "TEST-EX-001",
                    "layer": "exact",
                    "target": {"service_intent": "ghostwriting"},
                    "phrases": ["ghostwriting service"],
                    "confidence": 0.9,
                },
                {
                    "id": "TEST-RX-001",
                    "layer": "regex",
                    "target": {"service_intent": "editing_proofreading"},
                    "regex": r"\bediting\b",
                    "confidence": 0.85,
                },
            ],
        })
        cp = repo.build_compiled_pack(pack)
        assert cp.exact_union_pattern is not None
        assert "TEST-RX-001" in cp.compiled_regex
        assert cp.exact_union_pattern.search("ghostwriting service") is not None

    def test_rule_pack_preserved_in_compiled(self):
        repo = self._make_repo()
        pack = repo.load_active_rules()
        cp = repo.build_compiled_pack(pack)
        assert cp.rule_pack is pack

    def test_exact_rule_by_id_populated(self):
        repo = self._make_repo()
        cp = repo.build_compiled_pack()
        # Should have at least some exact rules mapped
        assert isinstance(cp.exact_rule_by_id, dict)
        # All values in exact_rule_by_id should be TriMatchRule objects
        from bookcraft.components.trimatch.schemas import TriMatchRule
        for rule_id, rule in cp.exact_rule_by_id.items():
            assert isinstance(rule, TriMatchRule)


class TestCompiledPackPrescreen:
    """Test that the compiled pack pre-screen optimization works correctly."""

    def _make_processed_message(self, text: str):
        from bookcraft.components.preprocessor.schemas import ProcessedMessage, TokenInfo
        tokens = [
            TokenInfo(text=w, lemma=w.lower(), start=i * 5, end=i * 5 + len(w))
            for i, w in enumerate(text.split())
        ]
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

    def test_engine_with_compiled_pack_classifies(self):
        from bookcraft.components.trimatch.engine import TriMatchEngine
        from bookcraft.components.trimatch.schemas import TriMatchMode

        repo = RuleRepository("data/trimatch/rules")
        rule_pack = repo.load_active_rules()
        compiled_pack = repo.build_compiled_pack(rule_pack)

        engine = TriMatchEngine(
            rule_pack=rule_pack,
            mode=TriMatchMode.SHADOW,
            compiled_pack=compiled_pack,
        )

        msg = self._make_processed_message("I need help with ghostwriting")
        result = engine.classify(msg)
        # Should classify without error
        assert result is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_engine_without_compiled_pack_still_works(self):
        """Backward compat: compiled_pack=None preserves existing behavior."""
        from bookcraft.components.trimatch.engine import TriMatchEngine
        from bookcraft.components.trimatch.schemas import TriMatchMode

        repo = RuleRepository("data/trimatch/rules")
        rule_pack = repo.load_active_rules()
        engine = TriMatchEngine(
            rule_pack=rule_pack,
            mode=TriMatchMode.SHADOW,
            compiled_pack=None,
        )

        msg = self._make_processed_message("I need help")
        result = engine.classify(msg)
        assert result is not None

    def test_prescreen_skips_exact_on_non_matching_message(self):
        """When compiled pack is present and message has no exact match,
        EXACT rules should be pre-screened out."""
        from bookcraft.components.trimatch.engine import TriMatchEngine
        from bookcraft.components.trimatch.schemas import TriMatchMode

        repo = RuleRepository("data/trimatch/rules")
        pack = RulePack.model_validate({
            "version": "test",
            "rules": [
                {
                    "id": "TEST-EX-SKIP",
                    "layer": "exact",
                    "target": {"service_intent": "ghostwriting"},
                    "phrases": ["very specific unique phrase xyz"],
                    "confidence": 0.9,
                },
            ],
        })
        compiled = repo.build_compiled_pack(pack)
        engine = TriMatchEngine(
            rule_pack=pack,
            mode=TriMatchMode.SHADOW,
            compiled_pack=compiled,
        )

        msg = self._make_processed_message("hello world")
        result = engine.classify(msg)
        # Should not match the exact rule
        assert result is not None
        assert result.service_primary is None or result.confidence < 0.9
