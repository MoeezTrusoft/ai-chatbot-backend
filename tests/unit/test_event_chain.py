from uuid import uuid4

from bookcraft.components.storage.events import calculate_event_hash
from bookcraft.components.storage.models import ThreadEvent


def test_event_hash_is_deterministic_for_canonical_payload_order() -> None:
    thread_id = uuid4()

    left = calculate_event_hash(
        thread_id=thread_id,
        sequence=1,
        event_type="state.updated",
        payload={"b": 2, "a": 1},
        previous_hash=None,
    )
    right = calculate_event_hash(
        thread_id=thread_id,
        sequence=1,
        event_type="state.updated",
        payload={"a": 1, "b": 2},
        previous_hash=None,
    )

    assert left == right


def test_mutating_old_event_breaks_chain_verification_logic() -> None:
    thread_id = uuid4()
    first_hash = calculate_event_hash(
        thread_id=thread_id,
        sequence=1,
        event_type="message.received",
        payload={"text": "hello"},
        previous_hash=None,
    )
    second = ThreadEvent(
        thread_id=thread_id,
        sequence=2,
        event_type="state.updated",
        payload={"field": "email"},
        previous_hash=first_hash,
        event_hash=calculate_event_hash(
            thread_id=thread_id,
            sequence=2,
            event_type="state.updated",
            payload={"field": "email"},
            previous_hash=first_hash,
        ),
    )

    tampered_hash = calculate_event_hash(
        thread_id=thread_id,
        sequence=1,
        event_type="message.received",
        payload={"text": "changed"},
        previous_hash=None,
    )

    assert second.previous_hash != tampered_hash

