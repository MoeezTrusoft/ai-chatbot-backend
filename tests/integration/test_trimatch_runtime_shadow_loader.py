from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service, build_trimatch_shadow_engine
from bookcraft.components.trimatch import TriMatchEngine
from bookcraft.infra.config import Settings


def _write_shadow_rule_pack(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "approved_candidates.rulepack.json").write_text(
        json.dumps(
            {
                "version": "test_shadow_rules.v1",
                "rules": [
                    {
                        "id": "shadow_video_trailer_marker",
                        "layer": "exact",
                        "target": {
                            "service_intent": "video_trailer",
                            "query_intent": None,
                            "funnel_stage": None,
                        },
                        "phrases": ["rare shadow video marker"],
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


def test_build_trimatch_shadow_engine_is_disabled_by_default(tmp_path: Path) -> None:
    _write_shadow_rule_pack(tmp_path)

    engine = build_trimatch_shadow_engine(
        Settings(app_env="test", trimatch_extra_rule_dir=str(tmp_path))
    )

    assert engine is None


def test_build_trimatch_shadow_engine_loads_extra_rules(tmp_path: Path) -> None:
    _write_shadow_rule_pack(tmp_path)

    engine = build_trimatch_shadow_engine(
        Settings(
            app_env="test",
            trimatch_extra_mode="shadow",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    assert isinstance(engine, TriMatchEngine)


@pytest.mark.asyncio
async def test_chat_service_records_extra_shadow_vote_without_changing_runtime(
    tmp_path: Path,
) -> None:
    _write_shadow_rule_pack(tmp_path)

    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="shadow",
            trimatch_extra_rule_dir=str(tmp_path),
        )
    )

    response = await service.handle_turn(
        ChatTurnRequest(
            message=("rare shadow video marker, but please just tell me how BookCraft works.")
        )
    )

    serialized_events = str(service.threads[response.thread_id].events)

    assert response.bubbles
    assert "trimatch.extra_shadow_voted" in serialized_events
    assert "video_trailer" in serialized_events
