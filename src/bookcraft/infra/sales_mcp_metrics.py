"""Prometheus counters for the bot half of the Website ↔ Chatbot funnel (S9.2.2).

Sales owns the server-side funnel counters (``src/lib/chatbot-metrics.js``); this
module is the bot's mirror, exported on the bot's own ``/metrics``:

* ``sales_mcp_calls_total{tool,outcome}`` — every ``tools/call`` this client made,
  split into ``ok`` and ``degraded`` (a failed call the turn survived);
* ``sales_mcp_tenant_resolution_misses_total{code}`` — turns whose site/tenant did
  not resolve to an allow-listed binding, which is the failure that silently
  disables the whole integration;
* ``sales_mcp_degradations_total{tool,code}`` — the degradations the caller fell
  back to the local path on, keyed by the bot-facing code.

Kept in its own module (rather than ``observability.py``) so ``sales_mcp`` can
count without importing the tracing stack. Recording never raises: a metrics
hiccup must not cost a turn.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import Counter

SALES_MCP_CALLS = Counter(
    "bookcraft_sales_mcp_calls_total",
    "Bot→Sales MCP tools/call by tool and outcome (S9.2.2).",
    ["tool", "outcome"],
)

SALES_MCP_TENANT_MISSES = Counter(
    "bookcraft_sales_mcp_tenant_resolution_misses_total",
    "Turns whose site/tenant did not resolve to an allow-listed binding (S9.2.2).",
    ["code"],
)

SALES_MCP_DEGRADATIONS = Counter(
    "bookcraft_sales_mcp_degradations_total",
    "Bot degradations to the local path keyed by the bot-facing code (S9.2.2).",
    ["tool", "code"],
)


def observe_sales_mcp_result(tool: str, result: Any) -> None:
    """Record one Sales MCP call outcome. Never raises."""
    try:
        code = str(getattr(result, "code", "") or "")
        ok = bool(getattr(result, "ok", False))
        SALES_MCP_CALLS.labels(tool=str(tool), outcome="ok" if ok else "degraded").inc()
        if code.startswith("sales_tenant"):
            SALES_MCP_TENANT_MISSES.labels(code=code).inc()
        if not ok and getattr(result, "degraded", False):
            SALES_MCP_DEGRADATIONS.labels(tool=str(tool), code=code or "unknown").inc()
    except Exception:  # pragma: no cover - observability must never break a turn
        return


__all__ = [
    "SALES_MCP_CALLS",
    "SALES_MCP_TENANT_MISSES",
    "SALES_MCP_DEGRADATIONS",
    "observe_sales_mcp_result",
]
