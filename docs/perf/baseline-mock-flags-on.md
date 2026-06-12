# Per-Stage Latency Baseline — mock-flags-on

- **Mode:** `mock`  ·  **Optimization flags:** **ON**
- **Measured turns:** 16 (warmup 3)
- **Captured:** 2026-06-12 (mock)

> Mock mode is CPU-bound and not a production proxy; run `--mode live` on
> staging with real keys for the production before/after numbers.

## End-to-end (wall-clock)

| P50 | P95 | P99 | max |
|---:|---:|---:|---:|
| 45.2 | 49.9 | 51.1 | 51.3 | (ms)

## Per-stage (from STAGE_LATENCY histogram, exact per-turn deltas)

| Stage | P50 (ms) | P95 (ms) | n |
|---|---:|---:|---:|
| context_build | 0.92 | 1.13 | 16 |
| extraction | 0.81 | 1.03 | 16 |
| intent_classification | 0.58 | 0.84 | 16 |
| response_generation | 0.04 | 0.05 | 16 |
| trg_update | 0.95 | 1.25 | 16 |
