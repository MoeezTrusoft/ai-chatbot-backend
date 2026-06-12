"""Tests for the shared HTTP client behaviour in bookcraft.components.llm.adapters.

These are acceptance tests written BEFORE the corresponding implementation changes.
Tests will FAIL until the implementation is fully applied. This is intentional —
they define the expected contract for the fix.

File organisation
-----------------
test_structured_does_not_recreate_client  — Fix: get_shared_client() must return a
    cached instance; httpx.AsyncClient.__init__ must not be called on repeated calls.
test_bounded_timeout_applied              — Fix: per-call timeout must use the adapter's
    read_timeout when it is set to a concrete float.
test_unbounded_timeout_when_none          — Fix: per-call timeout read must be None
    (unbounded) when the adapter's read_timeout is None.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel

from bookcraft.components.llm.adapters import AnthropicAdapter, get_shared_client, close_shared_client


class _EmptyModel(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _anthropic_adapter(read_timeout: float | None = None) -> AnthropicAdapter:
    return AnthropicAdapter(
        api_key="test-key",
        base_url="https://api.anthropic.com",
        timeout_seconds=30.0,
        read_timeout=read_timeout,
    )


# ---------------------------------------------------------------------------
# Shared-client reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_does_not_recreate_client() -> None:
    """httpx.AsyncClient must be instantiated exactly once across multiple structured() calls.

    The shared client is created on first use and cached in the module-level
    `_shared_client` variable.  Subsequent `structured()` calls must call
    `get_shared_client()` but must NOT create a new `httpx.AsyncClient` instance.
    """
    # Reset shared client state before the test.
    await close_shared_client()

    import bookcraft.components.llm.adapters as _adapters_module

    _init_call_count = 0
    _original_init = httpx.AsyncClient.__init__

    def _counting_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal _init_call_count
        _init_call_count += 1
        _original_init(self, *args, **kwargs)

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.raise_for_status = MagicMock()
    fake_response.text = '{"name": "avery"}'

    async def _fake_post(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake_response

    adapter = _anthropic_adapter()

    with patch.object(httpx.AsyncClient, "__init__", _counting_init):
        # Patch the actual HTTP post so no real network call is made.
        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            await adapter.structured(system="sys", user="usr", output_model=_EmptyModel, purpose="test")
            await adapter.structured(system="sys", user="usr", output_model=_EmptyModel, purpose="test")

    # AsyncClient.__init__ should only have been called once (initial creation).
    assert _init_call_count == 1, (
        f"Expected httpx.AsyncClient to be created exactly once; "
        f"got {_init_call_count} instantiations."
    )

    # Cleanup.
    await close_shared_client()


# ---------------------------------------------------------------------------
# Per-call timeout propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounded_timeout_applied() -> None:
    """When read_timeout=8.0, the per-call httpx.Timeout must have read=8.0.

    The AnthropicAdapter constructs a `call_timeout` using `self.read_timeout`;
    this test asserts the correct value reaches the actual HTTP post call.
    """
    await close_shared_client()

    captured_timeouts: list[httpx.Timeout] = []

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.raise_for_status = MagicMock()
    fake_response.text = '{"name": "avery"}'

    async def _capture_post(url: str, *, headers, json, timeout):  # type: ignore[no-untyped-def]
        if isinstance(timeout, httpx.Timeout):
            captured_timeouts.append(timeout)
        return fake_response

    adapter = _anthropic_adapter(read_timeout=8.0)

    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_capture_post)):
        await adapter.structured(system="sys", user="usr", output_model=_EmptyModel, purpose="test")

    assert captured_timeouts, "No timeout was captured — patch may not have intercepted the call."
    assert captured_timeouts[0].read == pytest.approx(8.0), (
        f"Expected read timeout 8.0, got {captured_timeouts[0].read}"
    )

    await close_shared_client()


@pytest.mark.asyncio
async def test_unbounded_timeout_when_none() -> None:
    """When read_timeout=None (default), the per-call httpx.Timeout must have read=None.

    None signals an unbounded (infinite) read window, which is correct for long
    LLM responses that may take tens of seconds to stream.
    """
    await close_shared_client()

    captured_timeouts: list[httpx.Timeout] = []

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.raise_for_status = MagicMock()
    fake_response.text = '{"name": "avery"}'

    async def _capture_post(url: str, *, headers, json, timeout):  # type: ignore[no-untyped-def]
        if isinstance(timeout, httpx.Timeout):
            captured_timeouts.append(timeout)
        return fake_response

    adapter = _anthropic_adapter(read_timeout=None)

    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_capture_post)):
        await adapter.structured(system="sys", user="usr", output_model=_EmptyModel, purpose="test")

    assert captured_timeouts, "No timeout was captured — patch may not have intercepted the call."
    assert captured_timeouts[0].read is None, (
        f"Expected read timeout None (unbounded), got {captured_timeouts[0].read}"
    )

    await close_shared_client()
