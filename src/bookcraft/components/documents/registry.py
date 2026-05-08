from __future__ import annotations

import hashlib
from pathlib import Path

from bookcraft.components.documents.schemas import DocumentKind, TemplateRecord


class DocumentTemplateRegistry:
    def __init__(self, template_dir: str | Path) -> None:
        self.template_dir = Path(template_dir)

    def get(self, kind: DocumentKind) -> TemplateRecord:
        if kind == DocumentKind.NDA:
            path = self.template_dir / "nda" / "nda_v1.ejs"
            version = "nda_v1"
        else:
            path = self.template_dir / "agreement" / "service_agreement_v1.ejs"
            version = "service_agreement_v1"
        if not path.exists():
            raise FileNotFoundError(path)
        return TemplateRecord(kind=kind, version=version, path=path, checksum=_sha256_file(path))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
