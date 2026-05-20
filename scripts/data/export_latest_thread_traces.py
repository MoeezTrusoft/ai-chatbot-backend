"""Export the latest thread traces from a running BookCraft instance.

Usage:
    python scripts/data/export_latest_thread_traces.py \
        --base-url http://localhost:8000 \
        --limit 10 \
        --trace-limit 500 \
        --output-dir reports/thread_exports

Reads BOOKCRAFT_ADMIN_ANALYSIS_TOKEN from the environment for auth.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    print("httpx is required: uv pip install httpx", file=sys.stderr)
    raise

_MAX_TRACE_LIMIT = 500


def _trace_rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if isinstance(payload, dict):
        for key in ("traces", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]

    return []


def _thread_id_from_trace(row: dict[str, Any]) -> str:
    thread = row.get("thread")
    nested_id = thread.get("id") if isinstance(thread, dict) else None
    return str(row.get("thread_id") or row.get("threadId") or nested_id or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export latest thread traces")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--limit", type=int, default=10, help="Max threads to export")
    parser.add_argument(
        "--trace-limit",
        type=int,
        default=500,
        help=f"Max traces to fetch per thread (max {_MAX_TRACE_LIMIT})",
    )
    parser.add_argument("--output-dir", default="reports/thread_exports")
    args = parser.parse_args()

    token = os.environ.get("BOOKCRAFT_ADMIN_ANALYSIS_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    trace_limit = min(args.trace_limit, _MAX_TRACE_LIMIT)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.output_dir) / f"latest_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(base_url=args.base_url, headers=headers, timeout=30.0)

    # 1. Fetch latest traces list.
    print(f"Fetching latest traces (limit={trace_limit}) …")
    try:
        resp = client.get(
            "/api/admin/analysis/traces/latest",
            params={"limit": trace_limit},
        )
        resp.raise_for_status()
        latest_rows = _trace_rows_from_payload(resp.json())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR fetching latest traces: {exc}", file=sys.stderr)
        return 1

    # 2. Extract unique thread IDs (preserve order, up to --limit).
    seen: set[str] = set()
    thread_ids: list[str] = []
    for row in latest_rows:
        tid = _thread_id_from_trace(row)
        if tid and tid not in seen:
            seen.add(tid)
            thread_ids.append(tid)
            if len(thread_ids) >= args.limit:
                break

    print(f"Found {len(thread_ids)} unique thread(s) to export.")

    # 3. Fetch per-thread traces and write JSON.
    combined: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for tid in thread_ids:
        try:
            resp = client.get(
                f"/api/admin/analysis/traces/{tid}",
                params={"limit": trace_limit},
            )
            resp.raise_for_status()
            rows = _trace_rows_from_payload(resp.json())
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: failed to fetch thread {tid}: {exc}", file=sys.stderr)
            errors.append({"thread_id": tid, "error": str(exc)})
            continue

        if not rows:
            print(f"  thread {tid[:8]}: no traces")
            errors.append({"thread_id": tid, "error": "no_traces"})
            continue

        thread_export = {"thread_id": tid, "traces": rows}
        thread_file = out_dir / f"{tid}.json"
        thread_file.write_text(
            json.dumps(thread_export, indent=2, default=str) + "\n", encoding="utf-8"
        )
        combined.append(thread_export)
        print(f"  thread {tid[:8]}: {len(rows)} traces → {thread_file.name}")

    # 4. Write combined JSON — dict-keyed by thread_id for stable schema.
    threads_dict: dict[str, Any] = {t["thread_id"]: t for t in combined}
    combined_path = out_dir / "latest_threads_combined.json"
    combined_path.write_text(
        json.dumps(
            {
                "exported_at": stamp,
                "thread_count": len(combined),
                "threads": threads_dict,
                "errors": errors,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nExported {len(combined)} thread(s) with {len(errors)} error(s).")
    print(f"output_dir={out_dir}")
    print(f"combined={combined_path}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
