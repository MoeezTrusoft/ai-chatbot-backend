from __future__ import annotations

import json

from bookcraft.components.funnel_signal import partition_source
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.trimatch import TriMatchEngine, TriMatchMode
from bookcraft.components.trimatch.schemas import TriMatchLayer
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    _, rule_pack = partition_source(settings.funnel_rule_source_path)
    engine = TriMatchEngine(
        rule_pack=rule_pack,
        mode=TriMatchMode.SHADOW,
        shortcut_layers={TriMatchLayer.EXACT, TriMatchLayer.REGEX, TriMatchLayer.PATTERN},
        funnel_stage_weight=0.0,
    )
    result = engine.classify(
        ProcessedMessage(
            raw="Can you give me a quote for my manuscript?",
            normalized="can you give me a quote for my manuscript?",
            tokens=[],
            negation_spans=[],
            hedge_spans=[],
            counterfactual_spans=[],
            deterministic_atoms={},
            embedding=[],
            language="en",
            char_count=43,
        )
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if result.funnel_stage != "quote_requested":
        return 1
    if "funnel_stage" not in result.model_dump(mode="json")["shadow_only_dimensions"]:
        return 1
    print("funnel smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
