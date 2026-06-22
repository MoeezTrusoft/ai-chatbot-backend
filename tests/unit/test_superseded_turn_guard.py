"""Superseded-turn guard: a turn whose token is below the thread's persisted latest
must not overwrite the newer turn's state. The decision is made against the SHARED
persisted state.latest_turn_token, so it is correct across multiple uvicorn workers."""
from __future__ import annotations

from bookcraft.components.response.chat_schemas import ChatTurnRequest
from bookcraft.domain.state import ThreadState
from bookcraft.services.chat import ChatService


def test_turn_token_field_accepted() -> None:
    assert ChatTurnRequest(message="hi", turn_token=7).turn_token == 7
    assert ChatTurnRequest(message="hi").turn_token is None  # default not supplied


def test_state_carries_latest_turn_token() -> None:
    # The token lives in the persisted state (shared across workers), not in memory.
    assert ThreadState().latest_turn_token == 0


def test_none_token_never_superseded() -> None:
    assert ChatService._token_superseded(None, 0) is False
    assert ChatService._token_superseded(None, 99) is False


def test_token_below_persisted_latest_is_superseded() -> None:
    assert ChatService._token_superseded(1, 2) is True
    assert ChatService._token_superseded(3, 5) is True


def test_token_at_or_above_latest_not_superseded() -> None:
    assert ChatService._token_superseded(2, 2) is False
    assert ChatService._token_superseded(5, 3) is False
    assert ChatService._token_superseded(1, 0) is False  # fresh thread, latest 0


def test_none_persisted_latest_treated_as_zero() -> None:
    assert ChatService._token_superseded(1, None) is False  # 1 < 0 is False
