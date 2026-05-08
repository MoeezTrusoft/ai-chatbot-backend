from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from bookcraft.components.documents.registry import DocumentTemplateRegistry
from bookcraft.components.documents.renderer import StrictTemplateRenderer
from bookcraft.components.documents.schemas import (
    AgreementParams,
    DocumentGenerationResult,
    DocumentKind,
    DocumentStatus,
    NDAParams,
)
from bookcraft.components.documents.verifier import DocumentVerifier


class DocumentEngine:
    def __init__(
        self,
        *,
        registry: DocumentTemplateRegistry,
        output_dir: str | Path,
        pdf_rendering_enabled: bool = False,
    ) -> None:
        self.registry = registry
        self.output_dir = Path(output_dir)
        self.pdf_rendering_enabled = pdf_rendering_enabled
        self.renderer = StrictTemplateRenderer()
        self.verifier = DocumentVerifier()

    def generate_nda(self, params: NDAParams) -> DocumentGenerationResult:
        return self._generate(DocumentKind.NDA, params.model_dump(by_alias=True, mode="json"))

    def generate_agreement(self, params: AgreementParams) -> DocumentGenerationResult:
        return self._generate(DocumentKind.AGREEMENT, params.model_dump(by_alias=True, mode="json"))

    def _generate(self, kind: DocumentKind, params: dict[str, object]) -> DocumentGenerationResult:
        record = self.registry.get(kind)
        rendered = self.renderer.render(record.path, params)
        parameter_hash = _hash_json(params)
        rendered_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        document_id = f"{kind.value}_{uuid4()}"
        target_dir = self.output_dir / kind.value
        target_dir.mkdir(parents=True, exist_ok=True)
        html_path = target_dir / f"{document_id}.html"
        html_path.write_text(rendered, encoding="utf-8")
        pdf_path: Path | None = None
        if self.pdf_rendering_enabled:
            from weasyprint import HTML  # type: ignore[import-untyped]

            pdf_path = target_dir / f"{document_id}.pdf"
            HTML(string=rendered, base_url=str(record.path.parent)).write_pdf(pdf_path)
        result = DocumentGenerationResult(
            document_id=document_id,
            kind=kind,
            status=DocumentStatus.GENERATED,
            template_version=record.version,
            parameter_hash=parameter_hash,
            rendered_hash=rendered_hash,
            html_path=str(html_path),
            pdf_path=str(pdf_path) if pdf_path else None,
        )
        verification_errors = self.verifier.verify(result, rendered)
        if verification_errors:
            return result.model_copy(
                update={
                    "status": DocumentStatus.REJECTED,
                    "verification_errors": verification_errors,
                    "human_review_required": True,
                }
            )
        return result.model_copy(update={"status": DocumentStatus.VERIFIED})


def _hash_json(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
