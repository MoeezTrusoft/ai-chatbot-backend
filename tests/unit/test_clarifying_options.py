"""Tests for ClarifyingOptionsBuilder."""

from __future__ import annotations

import pytest

from bookcraft.components.sales.clarifying_options import ClarifyingOptionsBuilder


@pytest.fixture
def builder() -> ClarifyingOptionsBuilder:
    return ClarifyingOptionsBuilder()


def test_service_options(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("service_options")
    assert result.found is True
    keys = [o.key for o in result.options]
    assert "ghostwriting" in keys
    assert "editing_proofreading" in keys
    assert "publishing_distribution" in keys
    assert "not_sure" in keys


def test_genre_options(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("genre_options")
    assert result.found is True
    keys = [o.key for o in result.options]
    assert "fiction" in keys
    assert "memoir" in keys
    assert "business_self_help" in keys
    assert "not_sure" in keys


def test_manuscript_stage_options(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("manuscript_stage_options")
    assert result.found is True
    keys = [o.key for o in result.options]
    assert "idea" in keys
    assert "rough_notes" in keys
    assert "outline" in keys
    assert "partial_draft" in keys
    assert "completed" in keys


def test_consultation_interest_options(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("consultation_interest")
    assert result.found is True
    assert len(result.options) >= 2


def test_preferred_call_time_options(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("preferred_call_time")
    assert result.found is True
    keys = [o.key for o in result.options]
    assert "morning" in keys or "afternoon" in keys


def test_name_and_contact_options(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("name_and_email_or_phone")
    assert result.found is True


def test_unknown_key_returns_empty(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("not_a_real_key")
    assert result.found is False
    assert result.options == []


def test_question_key_preserved(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("genre_options")
    assert result.question_key == "genre_options"


def test_all_keys_returns_list(builder: ClarifyingOptionsBuilder) -> None:
    keys = ClarifyingOptionsBuilder.all_keys()
    assert isinstance(keys, list)
    assert "service_options" in keys
    assert "genre_options" in keys
    assert "manuscript_stage_options" in keys
    assert "preferred_call_time" in keys


def test_options_have_labels(builder: ClarifyingOptionsBuilder) -> None:
    result = builder.build("service_options")
    for option in result.options:
        assert option.label
        assert option.key
