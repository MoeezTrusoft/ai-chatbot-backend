"""Tests for the unified field registry."""
from __future__ import annotations

import pytest

from bookcraft.domain.field_registry import (
    FIELD_REGISTRY,
    FieldDef,
    FieldType,
    get_forbidden_reasks,
    get_required_for_quote,
)


class TestFieldRegistry:
    def test_registry_has_entries(self):
        assert len(FIELD_REGISTRY) > 0

    def test_all_entries_are_field_def(self):
        for key, val in FIELD_REGISTRY.items():
            assert isinstance(val, FieldDef), f"{key} is not a FieldDef"

    def test_project_genre_present(self):
        assert "project.genre" in FIELD_REGISTRY

    def test_contact_email_is_pii(self):
        assert FIELD_REGISTRY["contact.email"].pii is True

    def test_contact_name_is_pii(self):
        assert FIELD_REGISTRY["contact.name"].pii is True

    def test_project_genre_not_pii(self):
        assert FIELD_REGISTRY["project.genre"].pii is False

    def test_all_paths_match_their_keys(self):
        for key, val in FIELD_REGISTRY.items():
            assert val.path == key, f"Key {key!r} doesn't match FieldDef.path {val.path!r}"

    def test_display_names_nonempty(self):
        for key, val in FIELD_REGISTRY.items():
            assert val.display_name, f"{key} has empty display_name"

    def test_project_word_count_present(self):
        assert "project.word_count" in FIELD_REGISTRY

    def test_contact_phone_present(self):
        assert "contact.phone" in FIELD_REGISTRY

    def test_contact_phone_is_pii(self):
        assert FIELD_REGISTRY["contact.phone"].pii is True

    def test_project_word_count_field_type_int(self):
        fd = FIELD_REGISTRY["project.word_count"]
        assert fd.field_type == "int"

    def test_project_genre_field_type_str(self):
        fd = FIELD_REGISTRY["project.genre"]
        assert fd.field_type == "str"


class TestGetRequiredForQuote:
    def test_returns_list_of_field_defs(self):
        result = get_required_for_quote()
        assert isinstance(result, list)
        for f in result:
            assert isinstance(f, FieldDef)

    def test_word_count_required_for_quote(self):
        paths = {f.path for f in get_required_for_quote()}
        assert "project.word_count" in paths

    def test_contact_fields_not_required_for_quote(self):
        paths = {f.path for f in get_required_for_quote()}
        assert "contact.email" not in paths
        assert "contact.name" not in paths

    def test_required_for_quote_nonempty(self):
        result = get_required_for_quote()
        assert len(result) > 0

    def test_all_required_have_required_for_quote_true(self):
        result = get_required_for_quote()
        for f in result:
            assert f.required_for_quote is True


class TestGetForbiddenReasks:
    def test_empty_paths_returns_empty(self):
        result = get_forbidden_reasks([])
        assert result == []

    def test_unknown_path_returns_empty(self):
        result = get_forbidden_reasks(["not.a.real.path"])
        assert result == []

    def test_known_path_returns_phrases(self):
        result = get_forbidden_reasks(["project.genre"])
        assert len(result) > 0
        assert any("genre" in p for p in result)

    def test_multiple_paths_combined(self):
        result = get_forbidden_reasks(["project.genre", "contact.email"])
        assert any("genre" in p for p in result)
        assert any("email" in p for p in result)

    def test_pii_path_has_reask_phrases(self):
        result = get_forbidden_reasks(["contact.phone"])
        assert len(result) > 0

    def test_returns_list(self):
        result = get_forbidden_reasks(["project.genre"])
        assert isinstance(result, list)

    def test_word_count_phrases(self):
        result = get_forbidden_reasks(["project.word_count"])
        assert len(result) > 0
        assert any("word" in p for p in result)


class TestFieldDefFrozen:
    def test_field_def_is_frozen_dataclass(self):
        """FieldDef is frozen=True so attribute assignment must raise."""
        fd = FieldDef(path="test.path", display_name="Test")
        with pytest.raises((AttributeError, TypeError)):
            fd.path = "new.path"  # type: ignore[misc]

    def test_field_def_default_values(self):
        fd = FieldDef(path="x.y", display_name="XY")
        assert fd.field_type == "str"
        assert fd.pii is False
        assert fd.required_for_quote is False
        assert fd.reask_phrases == []
        assert fd.description == ""
