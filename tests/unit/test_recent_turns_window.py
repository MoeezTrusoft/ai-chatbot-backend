"""Advisory item #1: rolling conversation-history window.

Verifies the ``ThreadState.recent_turns`` ring buffer trims to the last 5
exchanges (matching the persist logic in ``services/chat.py``), survives a
serialization round-trip, and that the generator renderer replays up to 5 turns.
"""

from __future__ import annotations

from bookcraft.components.response.generator import _recent_turns_prompt_section
from bookcraft.domain.state import ThreadState


def _append_and_trim(state: ThreadState, user: str, assistant: str) -> None:
    """Mirror the append+trim performed at the persist site in chat.py."""
    state.recent_turns.append((user[:300], assistant[:300]))
    if len(state.recent_turns) > 5:
        del state.recent_turns[:-5]


def test_recent_turns_defaults_to_empty() -> None:
    assert ThreadState().recent_turns == []


def test_recent_turns_accumulates_up_to_five() -> None:
    state = ThreadState()
    for i in range(1, 5):
        _append_and_trim(state, f"user {i}", f"assistant {i}")
    assert len(state.recent_turns) == 4
    assert state.recent_turns[0] == ("user 1", "assistant 1")
    assert state.recent_turns[-1] == ("user 4", "assistant 4")


def test_recent_turns_trims_to_last_five() -> None:
    state = ThreadState()
    for i in range(1, 9):  # 8 successive writes
        _append_and_trim(state, f"user {i}", f"assistant {i}")
    assert len(state.recent_turns) == 5
    # Only the newest 5 survive; the three oldest are dropped.
    assert state.recent_turns[0] == ("user 4", "assistant 4")
    assert state.recent_turns[-1] == ("user 8", "assistant 8")
    assert ("user 3", "assistant 3") not in state.recent_turns


def test_recent_turns_truncates_each_side_to_300_chars() -> None:
    state = ThreadState()
    _append_and_trim(state, "U" * 400, "A" * 400)
    user_text, asst_text = state.recent_turns[0]
    assert len(user_text) == 300
    assert len(asst_text) == 300


def test_recent_turns_survives_serialization_round_trip() -> None:
    state = ThreadState()
    for i in range(1, 4):
        _append_and_trim(state, f"user {i}", f"assistant {i}")
    reloaded = ThreadState.model_validate(state.model_dump())
    assert len(reloaded.recent_turns) == 3
    # Pydantic coerces the persisted JSON arrays back into (user, assistant) tuples.
    assert reloaded.recent_turns[0] == ("user 1", "assistant 1")
    assert reloaded.recent_turns[-1] == ("user 3", "assistant 3")


def test_generator_renderer_includes_up_to_five_turns() -> None:
    turns = [(f"turn {i} user", f"turn {i} assistant") for i in range(1, 7)]  # 6 turns
    section = _recent_turns_prompt_section(turns)
    # Newest 5 present, the oldest dropped.
    for i in range(2, 7):
        assert f"turn {i} user" in section
    assert "turn 1 user" not in section
