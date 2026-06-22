"""LLM service-specific metadata extraction → state.service_metadata, plus the
deterministic photo-style synonym fix."""

from __future__ import annotations

from bookcraft.components.extraction.llm_extractor import (
    _build_service_metadata_section,
    _facts_to_service_metadata,
)
from bookcraft.components.extraction.llm_schemas import LLMExtractedFacts, ServiceMetadataItem
from bookcraft.components.metadata.extractor import ServiceMetadataExtractor
from bookcraft.components.metadata.service_metadata import coerce_metadata_value
from bookcraft.domain.state import ThreadState
from bookcraft.services.chat import ChatService


# --- registry value validation --------------------------------------------------------

def test_coerce_enum_value_is_case_insensitive_and_canonical() -> None:
    assert coerce_metadata_value("cover_design_illustration", "visual_style", "Photographic") == "photographic"


def test_coerce_rejects_value_not_in_accepted_set() -> None:
    assert coerce_metadata_value("cover_design_illustration", "visual_style", "rainbow") is None


def test_coerce_boolean_and_freetext_fields() -> None:
    assert coerce_metadata_value("cover_design_illustration", "front_back_spine_needed", "yes") is True
    assert coerce_metadata_value("cover_design_illustration", "trim_size", "6x9") == "6x9"


def test_coerce_unknown_service_or_key_returns_none() -> None:
    assert coerce_metadata_value("nope", "visual_style", "photographic") is None
    assert coerce_metadata_value("cover_design_illustration", "nope", "x") is None


# --- LLM facts → validated service metadata -------------------------------------------

def test_facts_to_service_metadata_keeps_valid_highconf_only() -> None:
    facts = LLMExtractedFacts(
        service_metadata=[
            ServiceMetadataItem(key="visual_style", value="photographic", confidence=0.92),
            ServiceMetadataItem(key="visual_style", value="rainbow", confidence=0.95),  # invalid value
            ServiceMetadataItem(key="cover_format", value="paperback_cover", confidence=0.5),  # low conf
        ]
    )
    out = _facts_to_service_metadata(facts, "cover_design_illustration")
    assert out == {"cover_design_illustration": {"visual_style": "photographic"}}


def test_facts_to_service_metadata_noop_without_active_service() -> None:
    facts = LLMExtractedFacts(
        service_metadata=[ServiceMetadataItem(key="visual_style", value="photographic", confidence=0.9)]
    )
    assert _facts_to_service_metadata(facts, None) == {}


def test_service_metadata_section_lists_active_service_fields() -> None:
    section = _build_service_metadata_section("cover_design_illustration")
    assert "visual_style" in section and "cover_format" in section
    assert _build_service_metadata_section(None) == ""


# --- safe merge into state ------------------------------------------------------------

class _Res:
    def __init__(self, sm: dict) -> None:
        self.service_metadata = sm


def test_apply_llm_service_metadata_fills_only_missing_keys() -> None:
    state = ThreadState()
    state.service_metadata["cover_design_illustration"] = {"visual_style": "illustrated"}
    ChatService._apply_llm_service_metadata(
        state,
        _Res({"cover_design_illustration": {"visual_style": "photographic", "cover_format": "ebook_cover"}}),
    )
    svc = state.service_metadata["cover_design_illustration"]
    assert svc["visual_style"] == "illustrated"  # existing not overwritten
    assert svc["cover_format"] == "ebook_cover"  # new key filled


# --- deterministic photo-style synonyms (fast path) -----------------------------------

def test_photo_synonyms_map_to_photographic() -> None:
    ext = ServiceMetadataExtractor()
    for text in ["photo", "photo-composite", "photo composite", "photoreal", "photographic cover"]:
        res = ext.extract(text, active_service="cover_design_illustration")
        assert res.confirmed.get("cover_design_illustration", {}).get("visual_style") == "photographic", text
