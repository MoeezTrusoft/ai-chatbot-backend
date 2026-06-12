#!/usr/bin/env python
"""P0-T2 — per-stage latency baseline capturer.

Runs a fixed turn workload through the real pipeline and records **exact**
per-stage P50/P95 by snapshotting the ``STAGE_LATENCY`` histogram's per-stage
``_sum`` before and after each turn (the delta is that turn's time in that
stage). Also records end-to-end wall-clock percentiles. Writes a Markdown
baseline doc under ``docs/perf/``.

This is the tool the plan's P0-T2 asks for. Run it in **mock** mode for a
structural baseline now, or in **live** mode (real keys + TEI/Redis) on staging
to capture the production "before/after" numbers that the Phase-1 latency claims
require:

    cd ai_chatbot
    .venv/bin/python scripts/perf/capture_latency_baseline.py                       # mock, flags ON
    .venv/bin/python scripts/perf/capture_latency_baseline.py --no-optimize         # mock, flags OFF
    .venv/bin/python scripts/perf/capture_latency_baseline.py --mode live --turns 25 --label staging-on

Sequential by design: per-stage attribution from a process-global histogram is
only clean when one turn runs at a time. Use ``e2e_concurrent_validation.py`` for
the concurrency/throughput dimension.
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent  # ai_chatbot/
sys.path.insert(0, str(_ROOT / "src"))
os.chdir(_ROOT)

for _name in ("httpx", "httpcore", "opentelemetry", "bookcraft", "uvicorn"):
    logging.getLogger(_name).setLevel(logging.ERROR)
logging.basicConfig(level=logging.ERROR)

from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client import REGISTRY  # noqa: E402

from bookcraft.api.main import create_app  # noqa: E402
from bookcraft.infra.config import Settings  # noqa: E402

# The optimization flags whose latency impact P0-T2 measures (offline-safe set).
OPTIMIZE_FLAGS = [
    "llm_bounded_timeouts_enabled", "prompt_cache_enabled", "event_log_batching_enabled",
    "trg_background_persist_enabled", "llm_extraction_overlap_enabled",
    "trimatch_event_evidence_summary", "trg_event_rebuild_enabled",
    "project_fact_partitioning_enabled", "trimatch_compiled_index_enabled",
]

WORKLOAD = [
    "Hi, I'm writing a fantasy novel of about 80000 words.",
    "I'd like ghostwriting and editing.",
    "What is the timeline for editing?",
    "Actually, make it 100000 words.",
    "I want paperback and hardcover, ebook later.",
    "My budget is around $3000.",
    "Can you also do cover design?",
    "What's included in the full package?",
]


def make_settings(*, mode: str, optimize: bool) -> Settings:
    flags = {f: optimize for f in OPTIMIZE_FLAGS}
    return Settings(
        app_env="test" if mode == "mock" else "staging",
        api_auth_mode="off",
        log_level="ERROR",
        llm_provider_mode="mock" if mode == "mock" else "live",
        rate_limit_per_ip_per_minute=10_000_000,
        **flags,
    )


def _stage_sums() -> dict[str, float]:
    """Current per-stage cumulative seconds from the STAGE_LATENCY histogram."""
    out: dict[str, float] = {}
    for metric in REGISTRY.collect():
        if metric.name != "chat_stage_latency_seconds":
            continue
        for sample in metric.samples:
            if sample.name.endswith("_sum"):
                out[sample.labels.get("stage", "?")] = sample.value
    return out


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def run(*, mode: str, optimize: bool, turns: int, warmup: int) -> dict:
    app = create_app(make_settings(mode=mode, optimize=optimize))
    end_to_end: list[float] = []
    per_stage: dict[str, list[float]] = {}

    with TestClient(app) as client:
        thread_id: str | None = None

        def one_turn(msg: str) -> None:
            nonlocal thread_id
            payload = {"message": msg}
            if thread_id:
                payload["thread_id"] = thread_id
            r = client.post("/api/v1/chat/turn", json=payload)
            if r.status_code == 200:
                thread_id = r.json()["thread_id"]

        # Warmup (not measured) — fills caches, compiles rule packs, JITs paths.
        for i in range(warmup):
            one_turn(WORKLOAD[i % len(WORKLOAD)])

        for i in range(turns):
            msg = WORKLOAD[i % len(WORKLOAD)]
            before = _stage_sums()
            t0 = time.perf_counter()
            one_turn(msg)
            end_to_end.append((time.perf_counter() - t0) * 1000.0)
            after = _stage_sums()
            for stage_name, after_v in after.items():
                delta_ms = (after_v - before.get(stage_name, 0.0)) * 1000.0
                if delta_ms > 0:
                    per_stage.setdefault(stage_name, []).append(delta_ms)

    return {
        "mode": mode, "optimize": optimize, "turns": turns, "warmup": warmup,
        "e2e": {"p50": _pct(end_to_end, 50), "p95": _pct(end_to_end, 95),
                "p99": _pct(end_to_end, 99), "max": max(end_to_end) if end_to_end else 0.0},
        "stages": {
            name: {"p50": _pct(v, 50), "p95": _pct(v, 95), "n": len(v)}
            for name, v in sorted(per_stage.items())
        },
    }


def to_markdown(result: dict, *, label: str, generated_at: str) -> str:
    opt = "ON" if result["optimize"] else "OFF"
    lines = [
        f"# Per-Stage Latency Baseline — {label}",
        "",
        f"- **Mode:** `{result['mode']}`  ·  **Optimization flags:** **{opt}**",
        f"- **Measured turns:** {result['turns']} (warmup {result['warmup']})",
        f"- **Captured:** {generated_at}",
        "",
        "> Mock mode is CPU-bound and not a production proxy; run `--mode live` on",
        "> staging with real keys for the production before/after numbers.",
        "",
        "## End-to-end (wall-clock)",
        "",
        "| P50 | P95 | P99 | max |",
        "|---:|---:|---:|---:|",
        f"| {result['e2e']['p50']:.1f} | {result['e2e']['p95']:.1f} | "
        f"{result['e2e']['p99']:.1f} | {result['e2e']['max']:.1f} | (ms)",
        "",
        "## Per-stage (from STAGE_LATENCY histogram, exact per-turn deltas)",
        "",
        "| Stage | P50 (ms) | P95 (ms) | n |",
        "|---|---:|---:|---:|",
    ]
    for name, s in result["stages"].items():
        lines.append(f"| {name} | {s['p50']:.2f} | {s['p95']:.2f} | {s['n']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-stage latency baseline capturer (P0-T2)")
    ap.add_argument("--mode", choices=["mock", "live"], default="mock")
    ap.add_argument("--turns", type=int, default=24)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--no-optimize", action="store_true", help="run with upgrade flags OFF")
    ap.add_argument("--label", default=None)
    ap.add_argument("--out", default=None, help="output markdown path (default docs/perf/…)")
    ap.add_argument("--generated-at", default="unspecified",
                    help="timestamp string to stamp into the doc (env can't call time here)")
    args = ap.parse_args()

    optimize = not args.no_optimize
    label = args.label or f"{args.mode}-flags-{'on' if optimize else 'off'}"
    result = run(mode=args.mode, optimize=optimize, turns=args.turns, warmup=args.warmup)
    md = to_markdown(result, label=label, generated_at=args.generated_at)

    out = Path(args.out) if args.out else _ROOT / "docs" / "perf" / f"baseline-{label}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")

    print(md)
    print(f"\n→ wrote {out.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
