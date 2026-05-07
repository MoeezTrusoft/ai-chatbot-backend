# BookCraft Pricing & Timeline Configs v2.2 — Workbook-Perfected Replacement

This folder is intended to replace the current `data/pricing` or `v2` configuration folder in the BookCraft Pricing & Timeline Engine project.

## What was corrected

- Added workbook-backed add-ons for all 9 service configs.
- Removed config-only add-ons that were not present in the source workbooks.
- Corrected Marketing Premium Blitz / 12 Months from 44,999 to 43,999.
- Removed Marketing Enterprise Rollout auto-pricing because the workbook lists it in the input form but provides no package grid values. It is now marked custom-quote/human-review only.
- Corrected Marketing complexity from `0.06 / max 2.0` to workbook `0.05 / max 1.7`.
- Added workbook timeline-policy knobs and service-specific `timeline_tuning` metadata to every service.
- Corrected Cover Design base duration values and added interior illustration duration values.
- Added Publishing printing cost grid from the workbook.
- Replaced simplified complexity drivers with workbook-aligned drivers where available.
- Preserved deterministic-engine constraints: no LLM pricing, no RAG pricing, no invented numbers.

## Compatibility note

The Python config models now type and consume the v2.2 workbook-parity metadata fields:
`timeline_tuning`, `printing_cost_grid`, `enterprise_rollout_policy`, and
`complexity.service_specific_point_multipliers`.
