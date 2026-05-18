from __future__ import annotations

import pytest

from bookcraft.components.actions.slot_resolver import (
    contact_slots,
    is_confirmation_text,
    project_slots,
)
from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.state import ThreadState


def _message(text: str) -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[],
        language="en",
        char_count=len(text),
    )


def _contact_name(text: str) -> str | None:
    slots = contact_slots(
        state=ThreadState(),
        extraction=CombinedExtraction(),
        processed=_message(text),
    )
    return slots.get("name")


def _deadline(text: str) -> str | None:
    slots = project_slots(
        state=ThreadState(),
        extraction=CombinedExtraction(),
        processed=_message(text),
    )
    value = slots.get("deadline")
    return str(value) if value is not None else None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("My name is Maya Author and my email is maya@example.com", "Maya Author"),
        ("I am Maya Author, my email is maya@example.com", "Maya Author"),
        ("This is Maya Author. My phone is +1 555 123 4567", "Maya Author"),
        ("Maya Author here, I want to book a consultation", "Maya Author"),
    ],
)
def test_name_extraction_uses_strong_patterns(text: str, expected: str) -> None:
    assert _contact_name(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I am writing a memoir",
        "I am working on a children's book",
        "Use a professional editor",
        "I am looking for pricing",
        "I am self-publishing my book",
        "I am happy to proceed",
    ],
)
def test_name_extraction_rejects_fake_names(text: str) -> None:
    assert _contact_name(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("deadline is May 20, 2026", "May 20"),
        ("I need it by June 5", "June 5"),
    ],
)
def test_deadline_extraction_accepts_firm_values(text: str, expected: str) -> None:
    assert _deadline(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "deadline is whenever I'm ready",
        "no rush",
        "maybe later",
        "not sure about the deadline",
    ],
)
def test_deadline_extraction_rejects_vague_values(text: str) -> None:
    assert _deadline(text) is None


def test_deadline_extraction_keeps_specific_relative_duration() -> None:
    assert _deadline("I need it ready in 8 weeks.") == "8 weeks"


@pytest.mark.parametrize(
    "text",
    [
        "yes, book it",
        "confirm the booking",
        "go ahead and schedule it",
        "yes, schedule the consultation",
    ],
)
def test_confirmation_detection_accepts_booking_confirmations(text: str) -> None:
    assert is_confirmation_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "send me pricing details",
        "can you send the samples",
        "send me the NDA",
        "yes, send more info",
        "I want to know more before booking",
    ],
)
def test_confirmation_detection_rejects_accidental_send_phrases(text: str) -> None:
    assert not is_confirmation_text(text)
