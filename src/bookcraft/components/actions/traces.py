from __future__ import annotations

from typing import Any

from bookcraft.components.actions.schemas import ActionPlan, ActionResult


def action_trace_payload(
    plan: ActionPlan | None,
    result: ActionResult | None = None,
) -> dict[str, Any] | None:
    if plan is None:
        return None

    payload: dict[str, Any] = {
        "action_type": plan.action_type.value if plan.action_type else None,
        "status": plan.status.value,
        "missing_slots": plan.missing_slots,
        "recommended_follow_up_slots": plan.recommended_follow_up_slots,
        "collected_slots": plan.collected_slots,
        "confirmation_required": plan.confirmation_required,
        "pending_confirmation_key": plan.pending_confirmation_key,
        "customer_safe_prompt": plan.customer_safe_prompt,
        "reason": plan.reason,
    }

    if result is not None:
        payload["result"] = result.model_dump(mode="json")

    return payload
