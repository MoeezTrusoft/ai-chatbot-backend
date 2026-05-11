from bookcraft.components.storage.state_sanitizer import sanitize_thread_state_for_persistence
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


def test_sanitize_thread_state_redacts_personal_contact_values() -> None:
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

    serialized = str(snapshot)
    assert "Avery Author" not in serialized
    assert "avery@example.com" not in serialized
    assert "+1 555-123-4567" not in serialized
    assert snapshot["personal"]["name"]["value"] == "[REDACTED_NAME]"
    assert snapshot["personal"]["email"]["value"] == "[REDACTED_EMAIL]"
    assert snapshot["personal"]["phone"]["value"] == "[REDACTED_PHONE]"


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
