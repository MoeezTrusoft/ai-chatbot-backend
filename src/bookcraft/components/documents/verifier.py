from __future__ import annotations

import re
from pathlib import Path

from bookcraft.components.documents.registry import DocumentTemplateRegistry
from bookcraft.components.documents.safety import rendered_document_safety_errors
from bookcraft.components.documents.schemas import DocumentGenerationResult, DocumentKind


class TemplateVerifier:
    def __init__(self, registry: DocumentTemplateRegistry) -> None:
        self.registry = registry

    def verify_all(self) -> list[str]:
        errors: list[str] = []
        for kind in DocumentKind:
            record = self.registry.get(kind)
            text = record.path.read_text(encoding="utf-8")
            errors.extend(self._verify_template_text(record.path, text))
        return errors

    def _verify_template_text(self, path: Path, text: str) -> list[str]:
        errors: list[str] = []
        if "<%-" in text:
            errors.append(f"{path}: unescaped EJS output is not allowed")
        if not re.search(r"<%[= ]", text):
            errors.append(f"{path}: no EJS template fields found")
        if "REPLACE_WITH_APPROVED_VALUE" in text:
            errors.append(f"{path}: unresolved placeholder found")
        return errors


class DocumentVerifier:
    def verify(self, result: DocumentGenerationResult, rendered_html: str) -> list[str]:
        errors: list[str] = []
        if "<%" in rendered_html or "%>" in rendered_html:
            errors.append("unrendered template tag remains in HTML")
        if not rendered_html.strip().lower().startswith("<!doctype html>"):
            errors.append("rendered document is not an HTML document")
        if result.rendered_hash == result.parameter_hash:
            errors.append("rendered hash unexpectedly matches parameter hash")
        errors.extend(rendered_document_safety_errors(rendered_html))
        return errors
