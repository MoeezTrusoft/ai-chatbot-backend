from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service
from bookcraft.infra.config import Settings


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
                "version": "test_tiebreaker_considered_rules.v1",
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


@pytest.mark.asyncio
async def test_tiebreaker_candidate_logs_considered_without_applying(
    tmp_path: Path,
) -> None:
    _write_extra_rule_pack(
        tmp_path,
        rule_id="tiebreaker_video_marker",
        phrase="rare tiebreaker video marker",
        target={
            "service_intent": "video_trailer",
            "query_intent": None,
            "funnel_stage": None,
        },
    )

    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="tiebreaker_candidate",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    response = await service.handle_turn(
        ChatTurnRequest(
            message="I need proofreading help for my manuscript. rare tiebreaker video marker"
        )
    )

    event_types = _event_types(service, response.thread_id)
    payload = _last_payload(
        service,
        response.thread_id,
        "trimatch.extra_tiebreaker_considered",
    )

    assert "trimatch.extra_tiebreaker_considered" in event_types
    assert "trimatch.extra_advisory_recommended" not in event_types
    assert "trimatch.extra_shadow_voted" not in event_types

    assert payload["extra_tiebreaker"]["service_primary"] == "video_trailer"
    assert payload["decision"]["eligible"] is False
    assert payload["decision"]["applied"] is False
    assert payload["decision"]["dimension"] is None
    assert payload["decision"]["recommended_value"] is None
    assert payload["safety"]["side_effects_allowed"] is False

    assert response.intent is not None
    assert response.intent.service_primary is not None
    assert response.intent.service_primary.value == "editing_proofreading"


@pytest.mark.asyncio
async def test_tiebreaker_candidate_pricing_recommendation_is_blocked(
    tmp_path: Path,
) -> None:
    _write_extra_rule_pack(
        tmp_path,
        rule_id="tiebreaker_numbers_marker",
        phrase="rare tiebreaker numbers marker",
        target={
            "service_intent": None,
            "query_intent": "pricing_question",
            "funnel_stage": None,
        },
    )

    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="tiebreaker_candidate",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    response = await service.handle_turn(
        ChatTurnRequest(
            message="rare tiebreaker numbers marker. What does BookCraft do for authors?"
        )
    )

    payload = _last_payload(
        service,
        response.thread_id,
        "trimatch.extra_tiebreaker_considered",
    )

    assert payload["extra_tiebreaker"]["query_primary"] == "pricing_question"
    assert payload["decision"]["applied"] is False
    assert payload["safety"]["pricing_sensitive"] is True
    assert payload["safety"]["side_effects_allowed"] is False

    assert response.intent is not None
    assert response.intent.query_primary.value == "service_question"


@pytest.mark.asyncio
async def test_tiebreaker_candidate_document_recommendation_is_blocked(
    tmp_path: Path,
) -> None:
    _write_extra_rule_pack(
        tmp_path,
        rule_id="tiebreaker_document_marker",
        phrase="rare tiebreaker alpha marker",
        target={
            "service_intent": None,
            "query_intent": "agreement_request",
            "funnel_stage": None,
        },
    )

    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="tiebreaker_candidate",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    response = await service.handle_turn(
        ChatTurnRequest(message="rare tiebreaker alpha marker. What does BookCraft do for authors?")
    )

    payload = _last_payload(
        service,
        response.thread_id,
        "trimatch.extra_tiebreaker_considered",
    )

    assert payload["extra_tiebreaker"]["query_primary"] == "agreement_request"
    assert payload["decision"]["applied"] is False
    assert payload["safety"]["document_sensitive"] is True
    assert payload["safety"]["side_effects_allowed"] is False

    assert response.intent is not None
    assert response.intent.query_primary.value == "service_question"
