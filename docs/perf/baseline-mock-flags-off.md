# Per-Stage Latency Baseline — mock-flags-off

- **Mode:** `mock`  ·  **Optimization flags:** **OFF**
- **Measured turns:** 16 (warmup 3)
- **Captured:** 2026-06-12 (mock)

> Mock mode is CPU-bound and not a production proxy; run `--mode live` on
> staging with real keys for the production before/after numbers.

## End-to-end (wall-clock)

| P50 | P95 | P99 | max |
|---:|---:|---:|---:|
| 45.8 | 49.6 | 49.6 | 49.6 | (ms)

## Per-stage (from STAGE_LATENCY histogram, exact per-turn deltas)

| Stage | P50 (ms) | P95 (ms) | n |
|---|---:|---:|---:|
| context_build | 0.89 | 1.45 | 16 |
| extraction | 0.85 | 1.21 | 16 |
| intent_classification | 0.58 | 0.67 | 16 |
| response_generation | 0.04 | 0.05 | 16 |
| trg_update | 1.00 | 1.19 | 16 |
