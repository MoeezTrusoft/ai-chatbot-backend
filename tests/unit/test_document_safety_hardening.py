from pathlib import Path

import pytest

from bookcraft.components.documents.safety import (
    reject_placeholder_like_value,
    rendered_document_safety_errors,
    safe_document_output_path,
    safe_output_root,
)
from bookcraft.components.documents.schemas import DocumentKind


def test_safe_output_root_rejects_filesystem_root() -> None:
    with pytest.raises(ValueError):
        safe_output_root(Path("/"))


def test_safe_document_output_path_stays_under_root(tmp_path: Path) -> None:
    root = safe_output_root(tmp_path)
    path = safe_document_output_path(
        output_root=root,
        kind=DocumentKind.NDA,
        document_id="nda_123",
        suffix="html",
    )

    assert path.parent == root / "nda"
    assert path.name == "nda_123.html"


def test_safe_document_output_path_rejects_unsafe_document_id(tmp_path: Path) -> None:
    root = safe_output_root(tmp_path)

    with pytest.raises(ValueError):
        safe_document_output_path(
            output_root=root,
            kind=DocumentKind.NDA,
            document_id="../escape",
            suffix="html",
        )


def test_rendered_document_safety_errors_detect_placeholders_and_active_content() -> None:
    errors = rendered_document_safety_errors(
        "<!doctype html><html><body>TODO <script>alert(1)</script></body></html>"
    )

    assert any("placeholder" in error for error in errors)
    assert any("active content" in error for error in errors)


def test_reject_placeholder_like_value_rejects_tbd_values() -> None:
    with pytest.raises(ValueError):
        reject_placeholder_like_value("TBD", field_name="agreement fee field")

    assert (
        reject_placeholder_like_value(
            "fixture-engine-output",
            field_name="agreement fee field",
        )
        == "fixture-engine-output"
    )
