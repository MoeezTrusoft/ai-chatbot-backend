"""BookCraft RAG corpus pipeline and retrieval."""

from bookcraft.components.rag.index import RagIndexManager, rag_index_mapping
from bookcraft.components.rag.pipeline import build_chunks, extract_source_markdown, load_chunks
from bookcraft.components.rag.retriever import RagRetriever, reciprocal_rank_fusion
from bookcraft.components.rag.schemas import RagChunk, RagIngestionReport, RetrievedChunk
from bookcraft.components.rag.verifier import RagVerifier

__all__ = [
    "RagChunk",
    "RagIndexManager",
    "RagIngestionReport",
    "RagRetriever",
    "RagVerifier",
    "RetrievedChunk",
    "build_chunks",
    "extract_source_markdown",
    "load_chunks",
    "rag_index_mapping",
    "reciprocal_rank_fusion",
]
