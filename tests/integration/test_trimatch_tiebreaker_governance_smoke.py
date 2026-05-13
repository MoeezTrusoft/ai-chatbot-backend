from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service
from bookcraft.infra.config import Settings


def test_tiebreaker_candidate_mode_is_consideration_only() -> None:
    settings = Settings(app_env="test", trimatch_extra_mode="tiebreaker_candidate")

    assert settings.trimatch_extra_mode == "tiebreaker_candidate"


@pytest.mark.asyncio
async def test_advisory_mode_does_not_emit_tiebreaker_events(tmp_path: Path) -> None:
    _write_extra_rule_pack(
        tmp_path,
        rule_id="advisory_video_marker",
        phrase="rare advisory video marker",
        target={
            "service_intent": "video_trailer",
            "query_intent": None,
            "funnel_stage": None,
        },
    )

    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="advisory",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    response = await service.handle_turn(
        ChatTurnRequest(
            message="I need proofreading help for my manuscript. rare advisory video marker"
        )
    )

    event_types = _event_types(service, response.thread_id)

    assert "trimatch.extra_advisory_recommended" in event_types
    assert "trimatch.extra_tiebreaker_considered" not in event_types
    assert "trimatch.extra_shadow_voted" not in event_types

    payload = _last_payload(
        service,
        response.thread_id,
        "trimatch.extra_advisory_recommended",
    )

    assert payload["advisory_applied"] is False
    assert payload["side_effects_allowed"] is False
    assert response.intent is not None
    assert response.intent.service_primary is not None
    assert response.intent.service_primary.value == "editing_proofreading"


def test_tiebreaker_design_blocks_sensitive_query_intents() -> None:
    design = Path("docs/architecture/trimatch-tiebreaker-mode-design.md").read_text(
        encoding="utf-8"
    )

    required_blocked_intents = [
        "pricing_question",
        "timeline_question",
        "portfolio_request",
        "nda_request",
        "agreement_request",
        "payment_question",
        "complaint_or_objection",
        "ready_to_buy",
        "spam_or_abuse",
        "off_topic",
    ]

    for blocked_intent in required_blocked_intents:
        assert blocked_intent in design


def test_tiebreaker_design_requires_side_effects_to_stay_disabled() -> None:
    design = Path("docs/architecture/trimatch-tiebreaker-mode-design.md").read_text(
        encoding="utf-8"
    )

    assert "trimatch.extra_tiebreaker_considered" in design
    assert '"side_effects_allowed": false' in design
    assert '"applied": false' in design
    assert "Do not implement tiebreaker candidate mode yet." in design


def test_tiebreaker_readiness_runbook_requires_existing_safety_reports() -> None:
    runbook = Path("docs/runbooks/trimatch-tiebreaker-mode-readiness.md").read_text(
        encoding="utf-8"
    )

    required_commands = [
        "run_trimatch_shadow_runtime_review.py",
        "run_trimatch_advisory_audit_report.py",
        "build_trimatch_review_ingestion_audit_report.py",
        "validate_trimatch_reinforcement.py",
        "build_trimatch_calibration_report.py",
        "test_trimatch_reinforcement_governance_smoke.py",
        "test_trimatch_extra_advisory_mode.py",
        "test_trimatch_runtime_shadow_loader.py",
        "test_trimatch_disagreement_logging.py",
    ]

    for command in required_commands:
        assert command in runbook


def _write_extra_rule_pack(
    directory: Path,
    *,
    rule_id: str,
    phrase: str,
    target: dict[str, str | None],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "approved_candidates.rulepack.json").write_text(
        json.dumps(
            {
                "version": "test_tiebreaker_governance_rules.v1",
                "rules": [
                    {
                        "id": rule_id,
                        "layer": "exact",
                        "target": target,
                        "phrases": [phrase],
                        "regex": None,
                        "pattern": [],
                        "semantic_examples": [],
                        "confidence": 0.99,
                        "enabled": True,
                        "shortcut_allowed": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _event_types(service: Any, thread_id: object) -> list[str]:
    return [str(event["event_type"]) for event in service.threads[thread_id].events]


def _last_payload(service: Any, thread_id: object, event_type: str) -> dict[str, Any]:
    events = service.threads[thread_id].events
    for event in reversed(events):
        if event["event_type"] == event_type:
            payload = event["payload"]
            assert isinstance(payload, dict)
            return payload
    raise AssertionError(f"missing event: {event_type}")
