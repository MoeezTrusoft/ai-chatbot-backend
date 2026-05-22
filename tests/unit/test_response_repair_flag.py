"""Tests for response_repair_enabled feature flag (Step 1).

Verifies that with the flag off (default), a quality-failing first draft does NOT
trigger a call to response_generator.repair, and that with the flag on, it does.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bookcraft.components.response.quality_gate import ResponseQualityReport
from bookcraft.components.response.schemas import ResponseDraft


def _make_failing_quality_report() -> ResponseQualityReport:
    return ResponseQualityReport(
        passed=False,
        failures=["test_failure"],
        repair_instructions="Fix the test failure.",
        safe_fallback="Safe fallback text for test.",
    )


class _SpyGenerator:
    """Wraps a generator and records whether repair() was called."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.repair_call_count = 0

    async def generate(self, **kwargs: Any) -> ResponseDraft:
        return await self._inner.generate(**kwargs)

    async def repair(self, **kwargs: Any) -> ResponseDraft:
        self.repair_call_count += 1
        return ResponseDraft(text="Repaired response text.", source="claude_sonnet_repair")


@pytest.mark.asyncio
async def test_repair_not_called_when_flag_off() -> None:
    """With response_repair_enabled=False (default), repair must not be invoked."""
    from bookcraft.api.main import create_app
    from bookcraft.infra.config import Settings

    app = create_app(Settings(app_env="dev", api_auth_mode="off", response_repair_enabled=False))
    service = app.state.chat_service

    assert not service.response_repair_enabled

    spy = _SpyGenerator(service.response_generator)
    service.response_generator = spy  # type: ignore[assignment]

    mock_quality = MagicMock()
    mock_quality.passed = False
    mock_quality.failures = ["test_failure"]
    mock_quality.safe_fallback = "Safe fallback text for test."
    mock_quality.sales_tone = None
    mock_quality.repair_instructions = "Fix it."
    mock_quality.safe_repair_context = None

    with patch.object(service.response_quality_gate, "evaluate", return_value=mock_quality):
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/chat/turn", json={"message": "Tell me about your services."}
            )
            assert resp.status_code == 200

    assert spy.repair_call_count == 0, (
        "repair() must NOT be called when response_repair_enabled=False"
    )


@pytest.mark.asyncio
async def test_repair_called_when_flag_on() -> None:
    """With response_repair_enabled=True, repair IS invoked when quality fails."""
    from bookcraft.api.main import create_app
    from bookcraft.infra.config import Settings

    app = create_app(Settings(app_env="dev", api_auth_mode="off", response_repair_enabled=True))
    service = app.state.chat_service

    assert service.response_repair_enabled

    spy = _SpyGenerator(service.response_generator)
    service.response_generator = spy  # type: ignore[assignment]

    call_count = [0]

    def _side_effect(**kwargs: Any) -> MagicMock:
        call_count[0] += 1
        m = MagicMock()
        m.passed = call_count[0] > 1  # first call fails, second passes
        m.failures = [] if call_count[0] > 1 else ["test_failure"]
        m.safe_fallback = "Safe fallback."
        m.sales_tone = None
        m.repair_instructions = "Fix it."
        m.safe_repair_context = None
        return m

    with patch.object(service.response_quality_gate, "evaluate", side_effect=_side_effect):
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/chat/turn", json={"message": "Tell me about your services."}
            )
            assert resp.status_code == 200

    assert spy.repair_call_count > 0, (
        "repair() should have been called when response_repair_enabled=True"
    )


def test_settings_default_repair_disabled() -> None:
    """The default for response_repair_enabled must be False."""
    from bookcraft.infra.config import Settings

    s = Settings()
    assert s.response_repair_enabled is False
