from __future__ import annotations

from dataclasses import dataclass

from bookcraft.components.actions.schemas import ActionPlan, ActionResult, ActionStatus


@dataclass(slots=True)
class SalesActionDispatcher:
    async def dispatch(self, plan: ActionPlan) -> ActionResult | None:
        if plan.action_type is None or plan.status not in {
            ActionStatus.READY,
            ActionStatus.NEEDS_CONFIRMATION,
        }:
            return None

        return ActionResult(
            action_type=plan.action_type,
            success=False,
            customer_safe_summary="This action is planned but not implemented yet.",
            internal_summary=(
                "Sales action dispatcher foundation only; concrete tools come in later PRs."
            ),
            error_code="not_implemented",
        )
