from __future__ import annotations

import re

from bookcraft.components.preprocessor.schemas import ProcessedMessage, TokenInfo
from bookcraft.components.trimatch import (
    RuleRepository,
    TriMatchEngine,
    TriMatchLayer,
    TriMatchMode,
)
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    engine = TriMatchEngine(
        rule_pack=RuleRepository(settings.trimatch_rule_dir).load_active_rules(),
        mode=TriMatchMode(settings.trimatch_mode),
        shortcut_layers={TriMatchLayer.EXACT, TriMatchLayer.REGEX, TriMatchLayer.PATTERN},
        shortcut_threshold=settings.trimatch_shortcut_threshold,
        funnel_stage_weight=settings.trimatch_funnel_stage_weight,
    )
    result = engine.classify(_processed("pricing quote how much does ghostwriting cost"))
    print(result.model_dump_json(indent=2))
    return 0


def _processed(text: str) -> ProcessedMessage:
    tokens: list[TokenInfo] = []
    for match in re.finditer(r"\b[\w']+\b", text):
        word = match.group(0)
        tokens.append(
            TokenInfo(text=word, lemma=word.casefold(), start=match.start(), end=match.end())
        )
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=tokens,
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[1.0],
        language="en",
        char_count=len(text),
    )


if __name__ == "__main__":
    raise SystemExit(main())
