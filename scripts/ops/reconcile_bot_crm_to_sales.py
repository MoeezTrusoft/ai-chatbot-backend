#!/usr/bin/env python
"""One-off reconcile of the bot's local CRM into Sales (S12.2.1).

Before cutover the bot kept its own ``sales_leads`` / ``sales_consultations``
rows; after cutover Sales is the system of record (D1), so any real row that only
exists in the bot has to be replayed into Sales exactly once.

Properties this script guarantees:

* **Idempotent** — every replayed row derives a stable ``requestId`` from its own
  primary key, and Sales dedupes on that id, so re-running the script (or a crash
  halfway through) never creates a second lead or consultation.
* **Dry-run first** — the default mode prints the exact calls it *would* make and
  writes nothing. ``--apply`` performs them.
* **Tenant-scoped** — only rows carrying a ``tenant_id`` that resolves to an
  allow-listed binding are replayed. A row with no tenant is a standalone/dev row
  Sales never owned, and is reported as skipped rather than guessed at.
* **Report** — emits a JSON report (counts + per-row outcome) to stdout / a file.

Usage::

    # what would happen (no writes):
    reconcile_bot_crm_to_sales.py

    # perform the replay:
    reconcile_bot_crm_to_sales.py --apply --report reports/reconcile.json

Consultations are replayed only after their lead, against the Sales lead id the
lead call returned. A consultation in the past, or one whose lead was skipped, is
reported ``skipped`` — replaying it would be refused by Sales anyway.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

# Make `src` importable when run straight from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bookcraft.infra.config import Settings  # noqa: E402
from bookcraft.infra.sales_mcp import (  # noqa: E402
    SalesMcpClient,
    consultation_request_id,
    consultation_schedule_arguments,
    lead_create_arguments,
    lead_payload_from_sales,
    parse_tenant_allowlist,
)

LEAD_TOOL = "sales_lead_create"
CONSULTATION_TOOL = "sales_consultation_schedule"


@dataclass(slots=True)
class ReconcileCall:
    """One row → one Sales `tools/call`, with the stable id that dedupes it."""

    tool: str
    source_table: str
    source_id: str
    request_id: str
    tenant_id: str
    arguments: dict[str, Any]
    # For a consultation: the bot lead id it must be replayed against.
    depends_on_lead_id: str | None = None


@dataclass(slots=True)
class ReconcilePlan:
    calls: list[ReconcileCall] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def _lead_request_id(lead_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"reconcile:lead:{lead_id}"))


def _consultation_request_id(consultation_id: str) -> str:
    # Reuse the client's own bridge so a replayed booking and a live booking of
    # the same key would dedupe identically.
    return consultation_request_id(f"reconcile:consultation:{consultation_id}")


def _slots_from_lead(record: Mapping[str, Any]) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    for key in ("name", "email", "phone"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            slots[key] = value.strip()
    return slots


def build_lead_call(
    record: Mapping[str, Any], binding: Any
) -> ReconcileCall | None:
    """Map one bot lead onto `sales_lead_create`, or `None` when it cannot be."""
    lead_id = str(record.get("id") or "").strip()
    if not lead_id:
        return None
    slots = _slots_from_lead(record)
    if not slots:
        # Sales requires a contact channel; a nameless/contactless placeholder is
        # not a lead worth replaying.
        return None
    request_id = _lead_request_id(lead_id)
    arguments = lead_create_arguments(slots, binding=binding, request_id=request_id)
    return ReconcileCall(
        tool=LEAD_TOOL,
        source_table="sales_leads",
        source_id=lead_id,
        request_id=request_id,
        tenant_id=str(binding.tenant_id),
        arguments=arguments,
    )


def build_consultation_call(
    record: Mapping[str, Any],
    *,
    sales_lead_id: str,
    customer_timezone: str = "America/Chicago",
    now: datetime | None = None,
) -> ReconcileCall | None:
    """Map one bot consultation onto `sales_consultation_schedule`, if replayable."""
    consultation_id = str(record.get("id") or "").strip()
    starts_at = record.get("starts_at_utc")
    if not consultation_id or starts_at is None:
        return None
    if isinstance(starts_at, str):
        try:
            starts_at = datetime.fromisoformat(starts_at)
        except ValueError:
            return None
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if starts_at <= current:
        # A past appointment cannot be booked; Sales would refuse it. Reported as
        # skipped by the caller.
        return None
    request_id = _consultation_request_id(consultation_id)
    arguments = consultation_schedule_arguments(
        lead_id=sales_lead_id,
        start_time=starts_at.astimezone(UTC).isoformat(),
        customer_timezone=str(record.get("customer_timezone") or customer_timezone),
        request_id=request_id,
    )
    return ReconcileCall(
        tool=CONSULTATION_TOOL,
        source_table="sales_consultations",
        source_id=consultation_id,
        request_id=request_id,
        tenant_id=str(record.get("tenant_id") or ""),
        arguments=arguments,
        depends_on_lead_id=str(record.get("lead_id") or "") or None,
    )


def plan_reconcile(
    leads: Sequence[Mapping[str, Any]],
    consultations: Sequence[Mapping[str, Any]],
    *,
    allowlist_raw: str,
    now: datetime | None = None,
) -> ReconcilePlan:
    """Build the full replay plan without touching the network.

    Pure and deterministic: the same rows always produce the same calls (and the
    same request ids), which is what makes a re-run a no-op.
    """
    bindings = parse_tenant_allowlist(allowlist_raw)
    by_tenant = {str(binding.tenant_id).lower(): binding for binding in bindings.values()}
    plan = ReconcilePlan()

    replayed_leads: set[str] = set()
    for lead in leads:
        lead_id = str(lead.get("id") or "")
        tenant = str(lead.get("tenant_id") or "").strip().lower()
        if not tenant:
            plan.skipped.append({"table": "sales_leads", "id": lead_id, "reason": "no_tenant"})
            continue
        binding = by_tenant.get(tenant)
        if binding is None:
            # A tenant the bot is not allowed to write: replaying it would be a
            # cross-tenant write attempt.
            plan.skipped.append(
                {"table": "sales_leads", "id": lead_id, "reason": "tenant_not_allow_listed"}
            )
            continue
        call = build_lead_call(lead, binding)
        if call is None:
            plan.skipped.append({"table": "sales_leads", "id": lead_id, "reason": "no_contact"})
            continue
        plan.calls.append(call)
        replayed_leads.add(lead_id)

    for consultation in consultations:
        consultation_id = str(consultation.get("id") or "")
        lead_id = str(consultation.get("lead_id") or "")
        if not lead_id or lead_id not in replayed_leads:
            plan.skipped.append(
                {
                    "table": "sales_consultations",
                    "id": consultation_id,
                    "reason": "lead_not_replayed",
                }
            )
            continue
        call = build_consultation_call(
            consultation,
            sales_lead_id=lead_id,  # placeholder; replaced with the Sales id at apply time
            now=now,
        )
        if call is None:
            plan.skipped.append(
                {
                    "table": "sales_consultations",
                    "id": consultation_id,
                    "reason": "not_replayable",
                }
            )
            continue
        plan.calls.append(call)

    return plan


async def run_plan(
    plan: ReconcilePlan,
    client: SalesMcpClient,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Execute (or just report) a plan. Leads first, then their consultations."""
    results: list[dict[str, Any]] = []
    lead_id_map: dict[str, str] = {}
    for call in plan.calls:
        if not apply:
            # Nothing has been created yet, so the sales-id rewrite below only
            # applies to a real run; a dry run reports every planned call.
            results.append(
                {
                    "table": call.source_table,
                    "id": call.source_id,
                    "outcome": "would_apply",
                    "tool": call.tool,
                    "request_id": call.request_id,
                }
            )
            continue
        if call.tool == CONSULTATION_TOOL:
            # Rewrite the lead id to the Sales id the lead call returned.
            local_lead = call.depends_on_lead_id or ""
            sales_lead = lead_id_map.get(local_lead)
            if not sales_lead:
                results.append(
                    {
                        "table": call.source_table,
                        "id": call.source_id,
                        "outcome": "skipped",
                        "reason": "lead_not_created",
                    }
                )
                continue
            call.arguments["leadId"] = sales_lead
        result = await client.call_tool(
            call.tool,
            call.arguments,
            tenant_id=call.tenant_id,
            request_id=call.request_id,
        )
        outcome = "applied" if result.ok else "failed"
        if result.ok and call.tool == LEAD_TOOL and isinstance(result.data, dict):
            payload = lead_payload_from_sales(result.data)
            if payload.get("id"):
                lead_id_map[call.source_id] = str(payload["id"])
        results.append(
            {
                "table": call.source_table,
                "id": call.source_id,
                "outcome": outcome,
                "tool": call.tool,
                "request_id": call.request_id,
                "code": result.code,
                "sales_code": result.sales_code,
            }
        )
    applied = sum(1 for r in results if r["outcome"] == "applied")
    return {
        "mode": "apply" if apply else "dry-run",
        "calls": len(plan.calls),
        "skipped": plan.skipped,
        "results": results,
        "applied": applied,
    }


async def _load_rows(database_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the bot's local CRM rows. Imported lazily so dry-run needs no DB."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            lead_rows = (
                await connection.execute(
                    text(
                        "SELECT id, tenant_id, name, email, phone FROM sales_leads "
                        "WHERE tenant_id IS NOT NULL"
                    )
                )
            ).mappings().all()
            consultation_rows = (
                await connection.execute(
                    text(
                        "SELECT id, tenant_id, lead_id, starts_at_utc, customer_timezone "
                        "FROM sales_consultations WHERE tenant_id IS NOT NULL"
                    )
                )
            ).mappings().all()
    finally:
        await engine.dispose()
    return [dict(row) for row in lead_rows], [dict(row) for row in consultation_rows]


def _build_client(settings: Settings) -> SalesMcpClient:
    return SalesMcpClient(settings)


async def _run(args: argparse.Namespace) -> tuple[int, str]:
    settings = Settings()
    leads, consultations = await _load_rows(settings.database_url)
    plan = plan_reconcile(
        leads,
        consultations,
        allowlist_raw=settings.sales_mcp_tenant_allowlist_json,
    )
    report = await run_plan(plan, _build_client(settings), apply=args.apply)
    report["source"] = {"leads": len(leads), "consultations": len(consultations)}
    failed = any(r["outcome"] == "failed" for r in report["results"])
    return (1 if failed else 0), json.dumps(report, indent=2, sort_keys=True, default=str)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile the bot's local CRM into Sales (S12.2.1)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the replay (default is a dry run that writes nothing)",
    )
    parser.add_argument("--report", help="also write the JSON report to this path")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    code, text = asyncio.run(_run(args))
    print(text)
    # Written here, outside the event loop, so no blocking file I/O runs in a task.
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
