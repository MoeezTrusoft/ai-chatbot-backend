from __future__ import annotations

import json

from bookcraft.components.documents import DocumentEngine, DocumentTemplateRegistry, NDAParams
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    engine = DocumentEngine(
        registry=DocumentTemplateRegistry(settings.document_template_dir),
        output_dir=settings.document_output_dir,
        pdf_rendering_enabled=False,
    )
    result = engine.generate_nda(
        NDAParams.model_validate(
            {
                "date": "May 8, 2026",
                "authorTitle": "Mr.",
                "authorFullName": "Test Author",
                "authorPhone": "555-0100",
                "authorEmail": "author@example.com",
                "signature": "Jerry Miller",
            }
        )
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if result.status != "verified":
        return 1
    print("document smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
