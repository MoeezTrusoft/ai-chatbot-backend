"""Live extraction tests using real Claude API calls.

These tests fire actual Anthropic API requests to verify that extraction
works end-to-end with real Claude responses — catching issues like bare-string
returns, confidence as string, and unexpected field shapes that unit tests miss.

Run only when you want to validate before a production deploy:
    pytest tests/integration/test_live_extraction.py -v -s

Requires ANTHROPIC_API_KEY in .env.
"""
from __future__ import annotations

import os
import sys

import pytest

# Skip entire module if running in CI without API key or if marked skip
pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


def _make_live_extractor():
    """Build a real LLMMetadataExtractor using the Anthropic adapter."""
    from bookcraft.components.extraction.llm_extractor import LLMMetadataExtractor
    from bookcraft.components.llm.adapters import AnthropicAdapter
    from bookcraft.infra.config import Settings

    settings = Settings()
    adapter = AnthropicAdapter(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
        timeout_seconds=settings.llm_request_timeout_seconds,
        model=settings.anthropic_sonnet_model,
    )
    return LLMMetadataExtractor(adapter=adapter)


def _empty_state():
    from bookcraft.domain.state import ThreadState
    return ThreadState()


# ─── Scenario 1: Babar Azam — name + phone provided in separate messages ───

@pytest.mark.asyncio
async def test_extracts_name_babar_azam():
    """
    Bot asked: 'what's your name and the best number to reach you?'
    Customer replied: 'Babar Azam'
    Expected: name = 'Babar Azam' extracted at high confidence.
    """
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="Babar Azam",
        assistant_text="To get that set up, what's your name and the best number to reach you?",
        state=_empty_state(),
    )
    names = [d for d in result.state_deltas if d.path == "personal.name"]
    assert names, f"No name extracted. All deltas: {result.state_deltas}"
    assert "babar" in names[0].value.lower(), f"Wrong name: {names[0].value}"
    print(f"\n✓ name extracted: '{names[0].value}' (confidence={names[0].confidence})")


@pytest.mark.asyncio
async def test_extracts_phone_888_765_4321():
    """
    Bot asked: 'what's the best number to reach you?'
    Customer replied: '888 765 4321'
    Expected: phone extracted.
    """
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="888 765 4321",
        assistant_text="Got it - and what's your name so I can get this consultation locked in?",
        state=_empty_state(),
    )
    phones = [d for d in result.state_deltas if d.path == "personal.phone"]
    assert phones, f"No phone extracted. Deltas: {result.state_deltas}"
    assert "888" in phones[0].value or "4321" in phones[0].value
    print(f"\n✓ phone extracted: '{phones[0].value}'")


# ─── Scenario 2: Cameroon Green — full message with name ───

@pytest.mark.asyncio
async def test_extracts_name_cameroon_green():
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="Cameroon Green",
        assistant_text="To get that set up, what's your name and the best number to reach you?",
        state=_empty_state(),
    )
    names = [d for d in result.state_deltas if d.path == "personal.name"]
    assert names, f"No name extracted."
    assert "cameroon" in names[0].value.lower() or "green" in names[0].value.lower()
    print(f"\n✓ name extracted: '{names[0].value}'")


# ─── Scenario 3: "Babar Azam is my name" — clarifying restatement ───

@pytest.mark.asyncio
async def test_extracts_name_from_clarifying_restatement():
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="Babar Azam is my name",
        assistant_text="Got it - and what's your name so I can get this consultation locked in?",
        state=_empty_state(),
    )
    names = [d for d in result.state_deltas if d.path == "personal.name"]
    assert names, "Name not extracted from 'Babar Azam is my name'"
    assert "babar" in names[0].value.lower()
    print(f"\n✓ clarifying restatement extracted: '{names[0].value}'")


# ─── Scenario 4: Timezone "eastern" / "central time" ───

@pytest.mark.asyncio
async def test_extracts_timezone_eastern():
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="eastern",
        assistant_text="What timezone are you in so we can schedule the call at a good time for you?",
        state=_empty_state(),
    )
    tzs = [d for d in result.state_deltas if d.path == "personal.timezone"]
    assert tzs, f"No timezone extracted. Deltas: {result.state_deltas}"
    val = tzs[0].value
    assert "new_york" in val.lower() or "eastern" in val.lower() or "america" in val.lower(), \
        f"Unexpected timezone value: {val}"
    print(f"\n✓ timezone extracted: '{val}'")


@pytest.mark.asyncio
async def test_extracts_timezone_central_time():
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="central time",
        assistant_text="What timezone are you in?",
        state=_empty_state(),
    )
    tzs = [d for d in result.state_deltas if d.path == "personal.timezone"]
    assert tzs, "No timezone extracted for 'central time'"
    val = tzs[0].value
    assert "chicago" in val.lower() or "central" in val.lower() or "america" in val.lower()
    print(f"\n✓ timezone 'central time' extracted: '{val}'")


# ─── Scenario 5: Preferred call time "8 jun 3 PM" ───

@pytest.mark.asyncio
async def test_no_crash_on_preferred_time():
    """
    The extractor doesn't have a preferred_call_time field — but it should
    not crash and should not emit any invalid delta.
    """
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="8 jun 3 PM",
        assistant_text="What day and time works best for a call? Our specialists are available Monday-Friday, 10 AM to 7 PM Central Time.",
        state=_empty_state(),
    )
    # Should not raise; may extract nothing or manuscript_status etc.
    print(f"\n✓ no crash on '8 jun 3 PM'. Deltas: {[d.path for d in result.state_deltas]}")


# ─── Scenario 6: Email extraction ───

@pytest.mark.asyncio
async def test_extracts_email():
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="cameroon.green@gmail.com",
        assistant_text="Perfect, Cameroon - what's the best email to send your consultation details to?",
        state=_empty_state(),
    )
    emails = [d for d in result.state_deltas if d.path == "personal.email"]
    assert emails, "No email extracted"
    assert "gmail.com" in emails[0].value
    print(f"\n✓ email extracted: '{emails[0].value}'")


# ─── Scenario 7: The Solarian Chronicles — genre and manuscript_status ───

@pytest.mark.asyncio
async def test_extracts_genre_from_long_message():
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text=(
            "The Solarian Chronicles is an epic fantasy and science-fiction saga set in the world of Solaria. "
            "I have lore, character arcs, how the main saga ends... just no middle part."
        ),
        assistant_text="Welcome to BookCraft! What are you working on?",
        state=_empty_state(),
    )
    deltas_by_path = {d.path: d for d in result.state_deltas}
    print(f"\n✓ Long message deltas: {list(deltas_by_path.keys())}")
    # Genre should be extracted
    assert "project.genre" in deltas_by_path, \
        f"No genre extracted. Got: {list(deltas_by_path.keys())}"
    genre_val = deltas_by_path["project.genre"].value
    assert "fantasy" in genre_val.lower() or "fiction" in genre_val.lower()
    print(f"  genre: '{genre_val}'")


# ─── Scenario 8: Manuscript status "I started from scratch" ───

@pytest.mark.asyncio
async def test_extracts_manuscript_status_from_scratch():
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="Starting from scratch, haven't written anything yet",
        assistant_text="Where does the project stand right now - do you have an outline, some draft chapters, or are you starting from scratch?",
        state=_empty_state(),
    )
    statuses = [d for d in result.state_deltas if d.path == "project.manuscript_status"]
    assert statuses, "No manuscript_status extracted"
    assert statuses[0].value == "not_started", f"Wrong status: {statuses[0].value}"
    print(f"\n✓ manuscript_status: '{statuses[0].value}'")


# ─── Scenario 9: "My name is Jake Biddulph and I prefer email" ───

@pytest.mark.asyncio
async def test_extracts_name_and_preferred_contact_from_compound_message():
    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="My name is Jake Biddulph and I prefer email",
        assistant_text="To connect you with the right specialist, what's your name and the best way to reach you?",
        state=_empty_state(),
    )
    deltas_by_path = {d.path: d for d in result.state_deltas}
    print(f"\n✓ compound message deltas: {list(deltas_by_path.keys())}")

    name_d = deltas_by_path.get("personal.name")
    assert name_d, "No name extracted"
    # Must NOT include "My name is" prefix or "and I prefer email" suffix
    assert "jake" in name_d.value.lower(), f"Expected Jake in name, got: '{name_d.value}'"
    assert "my name is" not in name_d.value.lower(), f"Name still contains prefix: '{name_d.value}'"
    assert "prefer" not in name_d.value.lower(), f"Name contains noise: '{name_d.value}'"
    print(f"  name: '{name_d.value}' ✓ (no prefix/suffix noise)")

    contact_d = deltas_by_path.get("personal.preferred_contact_method")
    if contact_d:
        assert "email" in contact_d.value.lower(), f"Wrong contact method: {contact_d.value}"
        print(f"  preferred_contact_method: '{contact_d.value}' ✓")


# ─── Scenario 10: Full flow with state already having phone ───

@pytest.mark.asyncio
async def test_does_not_reextract_known_facts():
    """Once phone is in state, the extractor should not re-extract it."""
    from bookcraft.domain.state import ThreadState
    from bookcraft.domain.meta import FieldMeta
    from bookcraft.domain.enums import Source

    state = ThreadState()
    state.personal.phone = FieldMeta(
        value="888 765 4321", confidence=0.92, source=Source.AI_EXTRACTED
    )

    extractor = _make_live_extractor()
    result = await extractor.extract(
        user_text="What is the cost?",
        assistant_text="Pricing varies by service. Want a free consultation?",
        state=state,
    )
    phones = [d for d in result.state_deltas if d.path == "personal.phone"]
    assert not phones, f"Should not re-extract phone already in state. Got: {phones}"
    print("\n✓ known phone not re-extracted")


# ─── Scenario 11: Validate model_validator coerces bare strings (no real API needed) ───

def test_model_validator_bare_string_coercion_no_api():
    """Verify our fix handles bare strings without calling the API."""
    from bookcraft.components.extraction.llm_schemas import LLMExtractedFacts

    # Simulate what Claude returns when it ignores the schema
    raw = {
        "name": "Babar Azam",           # bare string — was causing ValidationError
        "phone": "888 765 4321",        # bare string
        "genre": {
            "value": "epic fantasy",
            "confidence": "0.92",       # string confidence — was causing ValidationError
            "source_quote": "epic fantasy saga",
        },
        "unexpected_top_field": "ignored",  # should be ignored
    }

    facts = LLMExtractedFacts.model_validate(raw)

    assert facts.name is not None
    assert facts.name.value == "Babar Azam"
    assert facts.name.confidence == 0.85  # default assigned by coerce_bare_values

    assert facts.phone is not None
    assert facts.phone.value == "888 765 4321"

    assert facts.genre is not None
    assert facts.genre.value == "epic fantasy"
    assert facts.genre.confidence == 0.92  # string "0.92" coerced to float 0.92

    print("\n✓ bare string name coerced to ExtractedValue")
    print("✓ bare string phone coerced to ExtractedValue")
    print("✓ string confidence '0.92' coerced to float 0.92")
    print("✓ unexpected top-level field ignored")
