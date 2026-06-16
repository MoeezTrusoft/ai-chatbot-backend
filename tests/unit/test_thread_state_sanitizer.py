from bookcraft.components.storage.state_sanitizer import sanitize_thread_state_for_persistence
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


def test_sanitize_thread_state_preserves_contact_values() -> None:
    """Product policy: the customer's structured contact fields are PRESERVED in the
    persisted state (shown in the CSR AI State panel; needed for cross-turn lead
    assembly). Only the source message excerpts are redacted."""
    state = ThreadState()
    state.personal.name = FieldMeta[str](
        value="Avery Author",
        confidence=0.99,
        source="user_stated",
        raw_excerpt="My name is Avery Author",
    )
    state.personal.email = FieldMeta[str](
        value="avery@example.com",
        confidence=0.99,
        source="user_stated",
        raw_excerpt="Email me at avery@example.com",
    )
    state.personal.phone = FieldMeta[str](
        value="+1 555-123-4567",
        confidence=0.99,
        source="user_stated",
        raw_excerpt="Call +1 555-123-4567",
    )

    snapshot = sanitize_thread_state_for_persistence(state)

    # Structured contact values are preserved.
    assert snapshot["personal"]["name"]["value"] == "Avery Author"
    assert snapshot["personal"]["email"]["value"] == "avery@example.com"
    assert snapshot["personal"]["phone"]["value"] == "+1 555-123-4567"
    # …but the source message excerpts still have email/phone redacted.
    assert "avery@example.com" not in str(snapshot["personal"]["email"]["raw_excerpt"])
    assert "+1 555-123-4567" not in str(snapshot["personal"]["phone"]["raw_excerpt"])


def test_sanitize_thread_state_redacts_raw_excerpts_and_summary() -> None:
    state = ThreadState()
    state.project.synopsis = FieldMeta[str](
        value="A story about a detective. Contact author@example.com.",
        confidence=0.8,
        source="user_stated",
        raw_excerpt="Synopsis sent from author@example.com and +92 300 1234567",
    )
    state.rolling_summary = (
        "The author can be reached at author@example.com or +92 300 1234567. "
        "Private draft: https://example.com/private"
    )

    snapshot = sanitize_thread_state_for_persistence(state)

    serialized = str(snapshot)
    assert "author@example.com" not in serialized
    assert "+92 300 1234567" not in serialized
    assert "https://example.com/private" not in serialized
    assert "[REDACTED_EMAIL]" in serialized
    assert "[REDACTED_PHONE]" in serialized
    assert "[REDACTED_URL]" in serialized
