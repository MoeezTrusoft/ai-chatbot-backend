"""Tests for the ENTITY_INDEX_FAILURES Prometheus counter in chat service."""
from bookcraft.services.chat import ENTITY_INDEX_FAILURES
from prometheus_client import Counter


def test_entity_index_failures_is_counter():
    assert isinstance(ENTITY_INDEX_FAILURES, Counter)


def test_entity_index_failures_name():
    # The _name attribute stores the base name; the _total suffix is added during exposition.
    # The metric was registered as "entity_index_failures_total" so _name strips the suffix.
    assert "entity_index_failures" in ENTITY_INDEX_FAILURES._name


def test_entity_index_failures_label_structured_fact():
    # Should be able to label and increment without error
    ENTITY_INDEX_FAILURES.labels(kind="structured_fact").inc()


def test_entity_index_failures_label_free_text_fact():
    ENTITY_INDEX_FAILURES.labels(kind="free_text_fact").inc()


def test_entity_index_failures_can_be_incremented():
    # Verify increment doesn't raise
    counter = ENTITY_INDEX_FAILURES.labels(kind="test_kind")
    counter.inc()
    counter.inc(2)
