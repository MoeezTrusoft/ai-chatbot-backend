"""Rule K-06: learn from every single message, without the bot having asked.

These cover what the deterministic extraction must catch on its own, so learning
does not depend on an LLM call succeeding.
"""
from __future__ import annotations

import pytest

from bookcraft.components.preprocessor.detectors.manuscript_status_detector import (
    detect_manuscript_status,
)
from bookcraft.domain.enums import ManuscriptStatus


@pytest.mark.parametrize(
    "text",
    [
        "I've finished a 70,000-word thriller and need editing before I publish in March.",
        "I have finished my novel",
        "I finished writing my book last week",
        "We've just completed our memoir",
        "I finally finished the fantasy novel I started in college",
    ],
)
def test_a_finished_work_is_recognised_as_completed(text: str):
    assert detect_manuscript_status(text) == ManuscriptStatus.COMPLETED


@pytest.mark.parametrize(
    "text",
    [
        "I finished a chapter yesterday",
        "I've finished the first draft",
        "I want to finish my novel by March",
        "Once I have finished my book I will need a cover",
        "I finished reading a thriller about publishing",
    ],
)
def test_other_uses_of_finished_are_not_a_completed_manuscript(text: str):
    assert detect_manuscript_status(text) != ManuscriptStatus.COMPLETED


@pytest.mark.asyncio
async def test_hyphenated_counts_are_extracted():
    from bookcraft.api.main import create_app
    from bookcraft.infra.config import Settings

    service = create_app(Settings(app_env="test", api_auth_mode="off")).state.chat_service
    processed = await service.preprocessor.process(
        "I've finished a 70,000-word thriller, about a 280-page paperback", language="en"
    )
    atoms = processed.deterministic_atoms
    assert atoms.get("word_counts") == [70000]
    assert atoms.get("page_counts") == [280]
    assert atoms.get("manuscript_status") == "completed"
    assert atoms.get("genre") == "thriller"
