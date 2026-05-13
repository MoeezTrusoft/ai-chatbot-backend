from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service
from bookcraft.infra.config import Settings


def _write_shadow_rule_pack(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "approved_candidates.rulepack.json").write_text(
        json.dumps(
            {
                "version": "test_shadow_disagreement.v1",
                "rules": [
                    {
                        "id": "shadow_disagreement_video_trailer",
                        "layer": "exact",
                        "target": {
                            "service_intent": "video_trailer",
                            "query_intent": None,
                            "funnel_stage": None,
                        },
                        "phrases": ["rare disagreement marker"],
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


@pytest.mark.asyncio
async def test_chat_service_records_trimatch_disagreement_event(tmp_path: Path) -> None:
    _write_shadow_rule_pack(tmp_path)

    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="shadow",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    response = await service.handle_turn(
        ChatTurnRequest(message="I need editing help for my manuscript. rare disagreement marker")
    )

    events = service.threads[response.thread_id].events
    event_types = [event["event_type"] for event in events]

    assert response.bubbles
    assert "trimatch.extra_shadow_voted" in event_types
    assert "trimatch.disagreement_observed" in event_types

    disagreement_event = next(
        event for event in events if event["event_type"] == "trimatch.disagreement_observed"
    )
    payload = disagreement_event["payload"]

    assert payload["should_log"] is True
    assert payload["extra_shadow"]["service_primary"] == "video_trailer"
    assert payload["extra_shadow"]["evidence_count"] >= 1
