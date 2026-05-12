# Migration from `intent/hardening.py`

The long-term target is: no domain classification keyword should live in ad hoc Python hardening lists.

## Migration sequence

1. Snapshot current hardening terms.
2. Convert them into staged Tri-Match rules with `source=migrated_from_hardening` in metadata.
3. Run current diagnostics with old hardening and new staged rules.
4. Compare outputs.
5. Keep Python hardening as a safety net while rules run in shadow.
6. Promote approved rules to active.
7. Delete or shrink hardening once metrics prove parity.
