# Changelog — v2.2 Workbook-Perfected Configs

## High-confidence corrections

1. All workbook add-ons were imported into service configs.
2. All config-only add-ons identified in the audit were removed.
3. Direct mismatches were corrected:
   - Marketing `premium_blitz.12_months` = `43999`
   - Marketing `MCF` point multiplier = `0.05`
   - Marketing `MCF` max factor = `1.7`
   - Cover design duration grid matches workbook.
4. Timeline knobs were added for every service.
5. Publishing printing cost grid was added.
6. Interior Formatting quality buffer was set to `0` because the workbook references a formula but provides no explicit buffer table.

## Human-review/custom quote rules

- Marketing Enterprise Rollout remains available conceptually but is not auto-priced, because no workbook price grid exists.
- Publishing custom print size remains quote-only in the `printing_cost_grid`.

## Engine enhancement recommended

For exact workbook parity, the engine should consume:

- `timeline_tuning.service_multipliers`
- `timeline_tuning.campaign_duration_multipliers`
- `printing_cost_grid`
- `complexity.service_specific_point_multipliers` for Editing & Proofreading

The current v2.2 engine loads these configs and consumes the workbook-parity metadata
fields needed for service-specific complexity, timeline tuning, printing-cost grid
handling, and enterprise rollout review gating.
