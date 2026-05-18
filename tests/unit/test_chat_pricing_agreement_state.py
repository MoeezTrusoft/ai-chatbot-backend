from __future__ import annotations

from uuid import uuid4

from bookcraft.components.actions import ActionResult, ActionType
from bookcraft.domain.state import ThreadState
from bookcraft.services.chat import ChatService


def test_invalid_pricing_quote_does_not_become_agreement_ready() -> None:
    state = ThreadState()
    quote_id = str(uuid4())

    ChatService._apply_sales_action_result_to_state(
        state,
        ActionResult(
            action_type=ActionType.PRICE_QUOTE,
            success=True,
            result_id=quote_id,
            customer_safe_summary="I need missing details first.",
            payload={
                "status": "needs_clarification",
                "missing_fields": ["word_count"],
                "quote_output": {
                    "total_price_range": {
                        "low": {"amount": "0.00", "currency": "USD"},
                        "high": {"amount": "0.00", "currency": "USD"},
                    }
                },
            },
        ),
    )

    assert state.sales_actions.pricing.quote_id is None
    assert state.sales_actions.pricing.missing_fields == ["word_count"]


def test_nonzero_estimated_pricing_quote_becomes_agreement_ready() -> None:
    state = ThreadState()
    quote_id = str(uuid4())

    ChatService._apply_sales_action_result_to_state(
        state,
        ActionResult(
            action_type=ActionType.PRICE_QUOTE,
            success=True,
            result_id=quote_id,
            customer_safe_summary="Estimate ready.",
            payload={
                "status": "estimated",
                "missing_fields": [],
                "quote_output": {
                    "total_price_range": {
                        "low": {"amount": "1200.00", "currency": "USD"},
                        "high": {"amount": "1800.00", "currency": "USD"},
                    }
                },
            },
        ),
    )

    assert state.sales_actions.pricing.quote_id == quote_id
    assert state.sales_actions.pricing.missing_fields == []
