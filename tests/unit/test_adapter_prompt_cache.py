"""Locks the Anthropic prompt-cache contract for the ``system`` field.

Advisory item #3 ("add a second cache breakpoint") was investigated and found to
have no safe, behavior-preserving second breakpoint (see the docstring of
``_anthropic_system_payload``): the user message is volatile from byte 0, and the
system prompt's invariant body is preceded by a per-turn variable head, so a
second breakpoint would require reordering prompt content. These tests instead
lock the existing single-breakpoint contract so a future refactor cannot silently
break caching or leak the volatile date/time into the cached prefix.
"""

from bookcraft.components.llm.adapters import _anthropic_system_payload

SYSTEM = "STABLE SYSTEM PROMPT"
SUFFIX = "Current date and time: Monday, June 22, 2026."


def test_cache_enabled_system_is_single_cached_block_suffix_uncached() -> None:
    payload = _anthropic_system_payload(SYSTEM, SUFFIX, cache_enabled=True)

    assert isinstance(payload, list)
    assert len(payload) == 2

    cached, volatile = payload

    # Exactly one cache breakpoint: the whole stable system prompt.
    assert cached == {
        "type": "text",
        "text": SYSTEM,
        "cache_control": {"type": "ephemeral"},
    }

    # The volatile date/time suffix is the ONLY thing outside the cached block,
    # and it carries NO cache_control (it changes every minute).
    assert volatile == {"type": "text", "text": SUFFIX}
    assert "cache_control" not in volatile

    # Precisely one cache_control breakpoint across the whole payload.
    breakpoints = [b for b in payload if "cache_control" in b]
    assert len(breakpoints) == 1


def test_cache_enabled_without_suffix_is_lone_cached_block() -> None:
    payload = _anthropic_system_payload(SYSTEM, None, cache_enabled=True)

    assert payload == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]


def test_cache_enabled_ignores_empty_suffix() -> None:
    # A falsy suffix must not add an empty uncached block.
    payload = _anthropic_system_payload(SYSTEM, "", cache_enabled=True)

    assert payload == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]


def test_cache_disabled_returns_plain_concatenated_string() -> None:
    payload = _anthropic_system_payload(SYSTEM, SUFFIX, cache_enabled=False)

    # No cache_control, no block list — identical content the model still sees.
    assert payload == f"{SYSTEM}\n{SUFFIX}"


def test_cache_disabled_without_suffix_returns_bare_system_string() -> None:
    payload = _anthropic_system_payload(SYSTEM, None, cache_enabled=False)

    assert payload == SYSTEM


def test_cache_disabled_never_emits_cache_control_anywhere() -> None:
    # Guards the "no cache_control when prompt_cache_enabled is False" invariant
    # regardless of suffix presence.
    for suffix in (SUFFIX, None, ""):
        payload = _anthropic_system_payload(SYSTEM, suffix, cache_enabled=False)
        assert isinstance(payload, str)
        assert "cache_control" not in payload
