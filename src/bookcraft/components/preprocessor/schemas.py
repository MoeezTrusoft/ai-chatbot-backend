from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.preprocessor.negation_targets import NegationTarget


class TokenInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    lemma: str
    pos: str | None = None
    start: int
    end: int
    negated: bool = False
    hedged: bool = False
    counterfactual: bool = False


class Span(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    text: str
    cue: str


class ProcessedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw: str
    normalized: str
    tokens: list[TokenInfo]
    negation_spans: list[Span]
    hedge_spans: list[Span]
    counterfactual_spans: list[Span]
    deterministic_atoms: dict[str, object]
    embedding: list[float]
    language: str
    char_count: int
    negation_targets: list[NegationTarget] = Field(default_factory=list)
