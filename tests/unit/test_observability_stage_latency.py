"""Tests for the STAGE_LATENCY Prometheus histogram in observability.py."""
from bookcraft.infra.observability import STAGE_LATENCY
from prometheus_client import Histogram


def test_stage_latency_is_histogram():
    assert isinstance(STAGE_LATENCY, Histogram)


def test_stage_latency_name():
    assert STAGE_LATENCY._name == "chat_stage_latency_seconds"


def test_stage_latency_labels():
    # Should be able to create a labelled observation without error
    with STAGE_LATENCY.labels(stage="test_stage").time():
        pass  # no-op


def test_stage_latency_known_stages():
    known_stages = [
        "intent_classification",
        "extraction",
        "context_build",
        "response_generation",
        "state_persist",
        "trg_update",
    ]
    for stage in known_stages:
        with STAGE_LATENCY.labels(stage=stage).time():
            pass
