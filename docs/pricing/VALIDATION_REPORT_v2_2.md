# Validation Report — BookCraft Pricing & Timeline Configs v2.2

## Static validation

| Check | Result |
|---|---|
| Engine config load | Passed: 9 services loaded |
| Built-in config validator | Passed |
| Validation errors | 0 |
| Validation warnings | 0 |

## Workbook-backed add-on coverage

| Service | Expected from workbook | Config count | Result |
|---|---:|---:|---|
| audiobook_production | 20 | 20 | Passed |
| author_website | 24 | 24 | Passed |
| cover_design_illustration | 12 | 12 | Passed |
| editing_proofreading | 11 | 11 | Passed |
| interior_formatting | 7 | 7 | Passed |
| ghostwriting | 10 | 10 | Passed |
| marketing_promotion | 25 | 25 | Passed |
| publishing_distribution | 11 | 11 | Passed |
| video_trailer | 24 | 24 | Passed |

## Direct value corrections verified

| Check | Config | Workbook | Result |
|---|---:|---:|---|
| Marketing premium_blitz.12_months | 43999 | 43999 | Passed |
| Marketing MCF multiplier | 0.05 | 0.05 | Passed |
| Marketing MCF max | 1.7 | 1.7 | Passed |
| Cover front_only.standard duration | 3 | 3 | Passed |
| Cover color.full_page illustration duration | 2 | 2 | Passed |
| Publishing 6x9 hardcover_bw printing cost | 9.5 | 9.5 | Passed |

## Runtime smoke test

Loaded the configs through the v2.2 Python engine and generated a sample multi-service quote successfully using Editing, Marketing, and Cover Design. This confirms the configs are syntactically valid and compatible with the current loader.

## Remaining implementation note

The engine now consumes the workbook-parity metadata fields for `timeline_tuning`,
`printing_cost_grid`, `enterprise_rollout_policy`, and
`complexity.service_specific_point_multipliers`.
