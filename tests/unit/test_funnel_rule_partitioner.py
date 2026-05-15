from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from bookcraft.components.funnel_signal import (
    FunnelRulePartitioner,
    partition_source,
    verify_funnel_partition,
)
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.trimatch import TriMatchEngine, TriMatchMode
from bookcraft.components.trimatch.schemas import TriMatchLayer
from bookcraft.domain.enums import SalesStage


def test_funnel_partitioner_splits_user_language_crm_and_metadata_rules() -> None:
    report, rule_pack = partition_source("data/funnel/funnel_stage_intents.sample.json")

    assert report.user_language_count == 3
    assert report.crm_count == 1
    assert report.dropped_count == 1
    assert {rule.id for rule in rule_pack.rules} == {"FS-USER-001", "FS-USER-002", "FS-USER-003"}
    assert all(rule.target.funnel_stage is not None for rule in rule_pack.rules)
    assert all(not rule.shortcut_allowed for rule in rule_pack.rules)


def test_funnel_verifier_rejects_crm_leakage_into_trimatch_pack() -> None:
    report, rule_pack = partition_source("data/funnel/funnel_stage_intents.sample.json")
    report_with_crm_loaded = report.model_copy(
        update={"user_language_rules": [*report.user_language_rules, report.crm_rules[0]]}
    )
    crm_rule = FunnelRulePartitioner().to_trimatch_rule_pack(report_with_crm_loaded)

    errors = verify_funnel_partition(report, crm_rule)

    assert any("CRM rules leaked" in error for error in errors)


def test_funnel_verifier_accepts_seed_partition() -> None:
    report, rule_pack = partition_source("data/funnel/funnel_stage_intents.sample.json")

    assert verify_funnel_partition(report, rule_pack) == []


def test_invalid_stage_fails_source_validation(tmp_path: Path) -> None:
    bad_source = tmp_path / "bad_funnel.json"
    bad_source.write_text(
        """
        {
          "version": "bad",
          "rules": [
            {
              "id": "FS-BAD-001",
              "section": "Inbound Message - user language",
              "stage": "send_invoice",
              "layer": "exact",
              "phrases": ["send me an invoice"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    try:
        partition_source(bad_source)
    except ValidationError as exc:
        assert "send_invoice" in str(exc)
    else:  # pragma: no cover - defensive, the assertion above is the behavior.
        raise AssertionError("invalid funnel stage should fail validation")


def test_partitioned_funnel_rules_run_shadow_only_in_trimatch() -> None:
    _, rule_pack = partition_source("data/funnel/funnel_stage_intents.sample.json")
    engine = TriMatchEngine(
        rule_pack=rule_pack,
        mode=TriMatchMode.SHADOW,
        shortcut_layers={TriMatchLayer.EXACT, TriMatchLayer.REGEX, TriMatchLayer.PATTERN},
        funnel_stage_weight=0.0,
    )

    result = engine.classify(
        ProcessedMessage(
            raw="Can you give me a quote for my manuscript?",
            normalized="can you give me a quote for my manuscript?",
            tokens=[],
            negation_spans=[],
            hedge_spans=[],
            counterfactual_spans=[],
            deterministic_atoms={},
            embedding=[],
            language="en",
            char_count=43,
        )
    )

    assert result.funnel_stage == SalesStage.QUOTE_REQUESTED
    assert "funnel_stage" in result.model_dump(mode="json")["shadow_only_dimensions"]
