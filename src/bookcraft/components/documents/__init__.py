"""Strict-template Agreement and NDA Engine."""

from bookcraft.components.documents.engine import DocumentEngine
from bookcraft.components.documents.registry import DocumentTemplateRegistry
from bookcraft.components.documents.renderer import StrictTemplateRenderer
from bookcraft.components.documents.schemas import (
    AgreementParams,
    DocumentGenerationResult,
    DocumentKind,
    DocumentStatus,
    NDAParams,
    SelectedService,
    ServiceItem,
    TemplateRecord,
)
from bookcraft.components.documents.tools import register_document_tools
from bookcraft.components.documents.verifier import DocumentVerifier, TemplateVerifier

__all__ = [
    "AgreementParams",
    "DocumentEngine",
    "DocumentGenerationResult",
    "DocumentKind",
    "DocumentStatus",
    "DocumentTemplateRegistry",
    "DocumentVerifier",
    "NDAParams",
    "SelectedService",
    "ServiceItem",
    "StrictTemplateRenderer",
    "TemplateRecord",
    "TemplateVerifier",
    "register_document_tools",
]
