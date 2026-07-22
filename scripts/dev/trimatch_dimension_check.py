#!/usr/bin/env python
"""Per-dimension Tri-Match rule diagnostic — the calibration harness.

Loads ONE dimension's rule file + that dimension's eval, runs the real engine, and
reports precision/recall vs the verifier floors PLUS actionable diagnostics:
  - structural errors (funnel pricing/legal, invalid regex, semantic shortcut)
  - precision misfires: examples where a layer fired with the WRONG target
  - pattern misses: examples where the pattern layer did NOT fire-correctly, by label

Usage:
  python scripts/dev/trimatch_dimension_check.py \
      --dimension service_intent \
      --rules data/trimatch/staged/rules_army_v2/rules/service_intent_rules.v2.rules_army.json \
      --eval  data/trimatch/staged/rules_army_v2/eval/service_intent_eval.v2.rules_army.jsonl

Floors: precision >= 0.97 (exact/regex/pattern); recall exact>=0.20 regex>=0.35 pattern>=0.45.
Pattern matches an ORDERED SUBSEQUENCE of message lemmas. Keep patterns discriminative
(fire on one label, not others) or precision drops.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from bookcraft.components.trimatch.engine import TriMatchEngine
from bookcraft.components.trimatch.schemas import (
    EvalExample,
    RulePack,
    TriMatchDimension,
    TriMatchLayer,
    TriMatchMode,
)
from bookcraft.components.trimatch.verifier import _processed, _result_attr

_SHORTCUT = {TriMatchLayer.EXACT, TriMatchLayer.REGEX, TriMatchLayer.PATTERN}
_PREC_FLOOR = 0.97
_REC_FLOOR = {"exact": 0.20, "regex": 0.35, "pattern": 0.45}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dimension", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--show", default="pattern", help="layer to detail for misses")
    args = ap.parse_args()

    dim = TriMatchDimension(args.dimension)
    pack = RulePack.model_validate(json.loads(Path(args.rules).read_text()))
    examples = [
        EvalExample.model_validate(json.loads(line))
        for line in Path(args.eval).read_text().splitlines()
        if line.strip()
    ]
    examples = [e for e in examples if e.dimension == dim]

    # ---- structural checks (mirror TriMatchVerifier) ----
    import re

    structural: list[str] = []
    for rule in pack.rules:
        if rule.regex:
            try:
                re.compile(rule.regex)
            except re.error as exc:
                structural.append(f"{rule.id}: invalid regex: {exc}")
        if rule.target.dimension == TriMatchDimension.FUNNEL_STAGE:
            blob = " ".join([*rule.phrases, rule.regex or "", *rule.pattern]).casefold()
            if any(t in blob for t in ["price", "cost", "contract text", "legal clause"]):
                structural.append(f"{rule.id}: funnel-stage rule encodes pricing/legal")
        if rule.layer in {TriMatchLayer.SEMANTIC, TriMatchLayer.FUZZY} and rule.shortcut_allowed:
            structural.append(f"{rule.id}: semantic/fuzzy shortcut forbidden")

    engine = TriMatchEngine(rule_pack=pack, mode=TriMatchMode.SHADOW, shortcut_layers=_SHORTCUT)

    prec = defaultdict(lambda: [0, 0])   # layer -> [correct, fired]
    rec = defaultdict(lambda: [0, 0])    # layer -> [hit, total]
    misfires: dict[str, list] = defaultdict(list)
    pattern_misses: dict[str, list] = defaultdict(list)

    for ex in examples:
        result = engine.classify(_processed(ex.text))
        final = str(getattr(result, _result_attr(dim)))
        ok = final == ex.expected
        fired_layers = {e.layer for e in result.evidence if e.dimension == dim}
        for e in result.evidence:
            if e.dimension != dim:
                continue
            prec[e.layer.value][1] += 1
            if e.target == ex.expected:
                prec[e.layer.value][0] += 1
            elif e.layer.value == args.show:
                misfires[ex.expected].append(
                    f"{ex.text!r} -> fired {e.target!r} (want {ex.expected})"
                )
        for layer in (TriMatchLayer.EXACT, TriMatchLayer.REGEX, TriMatchLayer.PATTERN):
            rec[layer.value][1] += 1
            if ok and layer in fired_layers:
                rec[layer.value][0] += 1
        show_layer = TriMatchLayer(args.show)
        if not (ok and show_layer in fired_layers):
            pattern_misses[ex.expected].append(ex.text)

    def ratio(x):
        return x[0] / x[1] if x[1] else 0.0

    print(f"\n=== {dim.value}  ({len(examples)} eval examples, {len(pack.rules)} rules) ===")
    print("PRECISION (floor 0.97 for exact/regex/pattern):")
    prec_fail = []
    for layer in ("exact", "regex", "pattern"):
        v = ratio(prec[layer])
        bad = prec[layer][1] and v < _PREC_FLOOR
        print(f"  {layer:8} {v:.3f}  (fired {prec[layer][1]}) {'  <-- FAIL' if bad else ''}")
        if bad:
            prec_fail.append(layer)
    print("RECALL:")
    rec_fail = []
    for layer, floor in _REC_FLOOR.items():
        v = ratio(rec[layer])
        bad = v < floor
        print(f"  {layer:8} {v:.3f}  (>= {floor}) {'  <-- FAIL' if bad else ''}")
        if bad:
            rec_fail.append(layer)

    if structural:
        print(f"\nSTRUCTURAL ERRORS ({len(structural)}):")
        for s in structural:
            print("  ", s)

    if prec_fail:
        print(f"\n{args.show.upper()} MISFIRES to fix (hurt precision) — by intended label:")
        for label, items in sorted(misfires.items()):
            print(f"  [{label}]")
            for it in items[:8]:
                print("     ", it)

    print(f"\n{args.show.upper()}-LAYER MISSES to cover (raise recall) — by label:")
    for label, texts in sorted(pattern_misses.items()):
        print(f"  [{label}] {len(texts)} missed:")
        for t in texts[:6]:
            print("     ", repr(t))

    ok_all = not structural and not prec_fail and not rec_fail
    print(f"\nRESULT: {'PASS ✅' if ok_all else 'FAIL ❌'}"
          f"  (structural={len(structural)}, prec_fail={prec_fail}, rec_fail={rec_fail})")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
