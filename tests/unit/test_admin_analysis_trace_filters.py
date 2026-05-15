from bookcraft.api.admin_analysis import _filter_trace_rows, _trace_filter_payload


def _rows() -> list[dict[str, object]]:
    return [
        {
            "thread_id": "thread-a",
            "customer_id": "customer-1",
            "elapsed_ms": 261.46,
            "assistant": {"source": "clarification"},
            "intent": {
                "query_primary": "service_question",
                "service_primary": "editing_proofreading",
            },
            "runtime_atoms": {
                "services": [
                    "editing_proofreading",
                    "interior_formatting",
                ],
                "forbid_markers": [],
                "negated_terms": [],
            },
        },
        {
            "thread_id": "thread-b",
            "customer_id": "customer-2",
            "elapsed_ms": 52.0,
            "assistant": {"source": "pricing_engine"},
            "decision": {
                "final_vote": {
                    "query_primary": "pricing_question",
                    "service_primary": "ghostwriting",
                }
            },
            "runtime_atoms": {
                "services": ["ghostwriting"],
                "forbid_markers": ["price_number"],
                "negated_terms": ["quote"],
            },
        },
    ]


def test_filter_trace_rows_by_source() -> None:
    rows = _filter_trace_rows(
        _rows(),
        source="clarification",
        query_primary=None,
        service_primary=None,
        customer_id=None,
        min_latency_ms=None,
        has_forbid_markers=None,
        has_negated_terms=None,
    )

    assert len(rows) == 1
    assert rows[0]["thread_id"] == "thread-a"


def test_filter_trace_rows_by_intent_and_service() -> None:
    rows = _filter_trace_rows(
        _rows(),
        source=None,
        query_primary="pricing_question",
        service_primary="ghostwriting",
        customer_id=None,
        min_latency_ms=None,
        has_forbid_markers=None,
        has_negated_terms=None,
    )

    assert len(rows) == 1
    assert rows[0]["thread_id"] == "thread-b"


def test_filter_trace_rows_by_latency_and_markers() -> None:
    rows = _filter_trace_rows(
        _rows(),
        source=None,
        query_primary=None,
        service_primary=None,
        customer_id=None,
        min_latency_ms=100,
        has_forbid_markers=False,
        has_negated_terms=False,
    )

    assert len(rows) == 1
    assert rows[0]["thread_id"] == "thread-a"


def test_filter_trace_rows_by_customer_id() -> None:
    rows = _filter_trace_rows(
        _rows(),
        source=None,
        query_primary=None,
        service_primary=None,
        customer_id="customer-2",
        min_latency_ms=None,
        has_forbid_markers=True,
        has_negated_terms=True,
    )

    assert len(rows) == 1
    assert rows[0]["thread_id"] == "thread-b"


def test_trace_filter_payload_removes_empty_values() -> None:
    assert _trace_filter_payload(
        source="clarification",
        query_primary=None,
        service_primary="",
        min_latency_ms=100,
    ) == {
        "source": "clarification",
        "service_primary": "",
        "min_latency_ms": 100,
    }
