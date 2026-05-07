"""Shared preprocessing for every inbound user turn."""

from bookcraft.components.preprocessor.embedding import EmbeddingClient
from bookcraft.components.preprocessor.processor import SharedPreprocessor
from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span, TokenInfo
from bookcraft.components.preprocessor.sidecars import PreprocessorSidecars, load_sidecars

__all__ = [
    "EmbeddingClient",
    "PreprocessorSidecars",
    "ProcessedMessage",
    "SharedPreprocessor",
    "Span",
    "TokenInfo",
    "load_sidecars",
]
