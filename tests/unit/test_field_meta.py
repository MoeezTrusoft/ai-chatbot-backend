from datetime import datetime

from bookcraft.domain import FieldMeta, Source


def test_field_meta_serializes_provenance() -> None:
    extracted_at = datetime.fromisoformat("2026-05-07T00:00:00+00:00")
    field = FieldMeta[str](
        value="author@example.com",
        confidence=0.98,
        source=Source.USER_STATED,
        extracted_at=extracted_at,
        extracted_by="deterministic_preextractor.v1",
        raw_excerpt="my email is author@example.com",
    )

    dumped = field.model_dump(mode="json")

    assert dumped == {
        "value": "author@example.com",
        "confidence": 0.98,
        "source": "user_stated",
        "extracted_at": "2026-05-07T00:00:00Z",
        "extracted_by": "deterministic_preextractor.v1",
        "raw_excerpt": "my email is author@example.com",
    }
    assert field.is_high_confidence()


def test_field_meta_low_confidence_without_value_is_not_high_confidence() -> None:
    field = FieldMeta[str](value=None, confidence=1.0, source=Source.USER_STATED)

    assert not field.is_high_confidence()
