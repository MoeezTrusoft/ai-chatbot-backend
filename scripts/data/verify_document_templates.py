from __future__ import annotations

import json

from bookcraft.components.documents import DocumentTemplateRegistry, TemplateVerifier
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    errors = TemplateVerifier(DocumentTemplateRegistry(settings.document_template_dir)).verify_all()
    payload = {"valid": not errors, "errors": errors}
    print(json.dumps(payload, indent=2, sort_keys=True))
    if errors:
        return 1
    print("document template verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
