from pathlib import Path

import pytest

from bookcraft.components.documents import (
    AgreementParams,
    DocumentEngine,
    DocumentGenerationResult,
    DocumentKind,
    DocumentStatus,
    DocumentTemplateRegistry,
    DocumentVerifier,
)


def test_document_verifier_rejects_unresolved_placeholders_and_scripts() -> None:
    result = DocumentGenerationResult(
        document_id="nda_test",
        kind=DocumentKind.NDA,
        status=DocumentStatus.GENERATED,
        template_version="nda_v1",
        parameter_hash="params",
        rendered_hash="rendered",
    )

    errors = DocumentVerifier().verify(
        result,
        "<!doctype html><html><body>REPLACE_WITH_APPROVED_VALUE<script></script></body></html>",
    )

    assert any("placeholder" in error for error in errors)
    assert any("active content" in error for error in errors)


def test_agreement_params_reject_placeholder_fee_values() -> None:
    payload = _agreement_payload()
    payload["finalFee"] = "TBD"

    with pytest.raises(ValueError):
        AgreementParams.model_validate(payload)


def test_document_engine_writes_outputs_under_safe_root(tmp_path: Path) -> None:
    engine = DocumentEngine(
        registry=DocumentTemplateRegistry("data/templates"),
        output_dir=tmp_path,
        pdf_rendering_enabled=False,
    )

    result = engine.generate_nda(
        params=_nda_params(),
    )

    assert result.status == DocumentStatus.VERIFIED
    assert result.html_path is not None
    html_path = Path(result.html_path).resolve()
    html_path.relative_to(tmp_path.resolve())
    assert html_path.name.startswith("nda_")


def _nda_params():
    from bookcraft.components.documents import NDAParams

    return NDAParams.model_validate(
        {
            "date": "May 11, 2026",
            "authorTitle": "Ms.",
            "authorFullName": "Avery Author",
            "authorPhone": "555-0100",
            "authorEmail": "avery@example.com",
            "signature": "Jerry Miller",
        }
    )


def _agreement_payload() -> dict[str, object]:
    return {
        "logoPath": "",
        "effectiveDate": "May 11, 2026",
        "abbreviation": "Ms.",
        "clientFullName": "Avery Author",
        "clientPhone": "555-0100",
        "clientEmail": "avery@example.com",
        "clientLocation": "Houston, Texas",
        "filteredServices": [
            {
                "title": "Editing & Proofreading",
                "items": [
                    {
                        "title": "Copy Editing",
                        "description": "Line-level review from the selected service scope.",
                    }
                ],
            }
        ],
        "finalFee": "fixture-engine-output",
        "totalFee": "fixture-engine-output",
        "discountPercent": 0,
        "scheduleType": "Engine-approved payment schedule",
        "signature": "Jerry Miller",
        "agreementDate": "May 11, 2026",
    }
