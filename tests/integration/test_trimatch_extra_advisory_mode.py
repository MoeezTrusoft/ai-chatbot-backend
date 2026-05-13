from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service, build_trimatch_shadow_engine
from bookcraft.components.trimatch import TriMatchEngine
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
                "version": "test_advisory_rules.v1",
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


def _event_types(service: object, thread_id: object) -> list[str]:
    return [
        str(event["event_type"])
        for event in service.threads[thread_id].events  # type: ignore[attr-defined]
    ]


def _last_payload(service: object, thread_id: object, event_type: str) -> dict[str, Any]:
    events = service.threads[thread_id].events  # type: ignore[attr-defined]
    for event in reversed(events):
        if event["event_type"] == event_type:
            payload = event["payload"]
            assert isinstance(payload, dict)
            return payload
    raise AssertionError(f"missing event: {event_type}")


def _intent_snapshot(response: object) -> dict[str, str | None]:
    intent = response.intent  # type: ignore[attr-defined]
    assert intent is not None
    return {
        "query_primary": intent.query_primary.value,
        "service_primary": intent.service_primary.value if intent.service_primary else None,
        "funnel_stage": intent.funnel_stage.value,
    }


def test_build_extra_engine_loads_for_advisory_mode(tmp_path: Path) -> None:
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

    engine = build_trimatch_shadow_engine(
        Settings(
            app_env="test",
            trimatch_extra_mode="advisory",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    assert isinstance(engine, TriMatchEngine)


@pytest.mark.asyncio
async def test_advisory_logs_recommendation_without_shadow_vote(
    tmp_path: Path,
) -> None:
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
        ChatTurnRequest(message="I need editing help for my manuscript. rare advisory video marker")
    )

    event_types = _event_types(service, response.thread_id)
    payload = _last_payload(
        service,
        response.thread_id,
        "trimatch.extra_advisory_recommended",
    )

    assert "trimatch.extra_advisory_recommended" in event_types
    assert "trimatch.extra_shadow_voted" not in event_types
    assert payload["advisory_applied"] is False
    assert payload["side_effects_allowed"] is False
    assert payload["extra_advisory"]["service_primary"] == "video_trailer"
    assert payload["recommendation"]["recommended_value"] == "video_trailer"
    assert response.intent is not None
    assert response.intent.service_primary is not None
    assert response.intent.service_primary.value == "editing_proofreading"


@pytest.mark.asyncio
async def test_advisory_mode_does_not_change_final_intent(tmp_path: Path) -> None:
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

    message = "I need proofreading help for my completed manuscript. rare advisory video marker"

    off_service = build_chat_service(Settings(app_env="test"))
    advisory_service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="advisory",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    off_response = await off_service.handle_turn(ChatTurnRequest(message=message))
    advisory_response = await advisory_service.handle_turn(ChatTurnRequest(message=message))

    assert _intent_snapshot(advisory_response) == _intent_snapshot(off_response)
    assert "trimatch.extra_advisory_recommended" in _event_types(
        advisory_service,
        advisory_response.thread_id,
    )


@pytest.mark.asyncio
async def test_advisory_pricing_recommendation_has_no_side_effects(
    tmp_path: Path,
) -> None:
    _write_extra_rule_pack(
        tmp_path,
        rule_id="advisory_pricing_marker",
        phrase="rare advisory numbers marker",
        target={
            "service_intent": None,
            "query_intent": "pricing_question",
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
        ChatTurnRequest(message="rare advisory numbers marker. What does BookCraft do for authors?")
    )

    payload = _last_payload(
        service,
        response.thread_id,
        "trimatch.extra_advisory_recommended",
    )

    assert payload["advisory_applied"] is False
    assert payload["side_effects_allowed"] is False
    assert payload["extra_advisory"]["query_primary"] == "pricing_question"
    assert response.intent is not None
    assert response.intent.query_primary.value == "service_question"
