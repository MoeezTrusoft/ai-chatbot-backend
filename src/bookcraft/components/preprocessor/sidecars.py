import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PreprocessorSidecars:
    negation_cues: list[str]
    hedge_cues: list[str]
    counterfactual_cues: list[str]
    typography_replacements: dict[str, str]
    compound_variants: dict[str, str]


def load_sidecars(directory: str) -> PreprocessorSidecars:
    root = Path(directory)
    negation = _read_json(root / "_negation_cues.json")
    typography = _read_json(root / "_typography_normalization.json")
    compounds = _read_json(root / "_compound_word_variants.json")
    _verify_list(negation, "negation_cues")
    _verify_list(negation, "hedge_cues")
    _verify_list(negation, "counterfactual_cues")
    replacements = typography.get("replacements")
    variants = compounds.get("variants")
    if not isinstance(replacements, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in replacements.items()
    ):
        msg = "Typography sidecar replacements must be a string map."
        raise ValueError(msg)
    if not isinstance(variants, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in variants.items()
    ):
        msg = "Compound sidecar variants must be a string map."
        raise ValueError(msg)
    return PreprocessorSidecars(
        negation_cues=list(negation["negation_cues"]),
        hedge_cues=list(negation["hedge_cues"]),
        counterfactual_cues=list(negation["counterfactual_cues"]),
        typography_replacements=dict(replacements),
        compound_variants=dict(variants),
    )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        msg = f"Sidecar must be a JSON object: {path}"
        raise ValueError(msg)
    return loaded


def _verify_list(payload: dict[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        msg = f"Sidecar key {key} must be a non-empty string list."
        raise ValueError(msg)

