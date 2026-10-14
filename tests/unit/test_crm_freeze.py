"""The frozen-tenant local CRM write gate (S12.2.2)."""

from __future__ import annotations

from uuid import UUID

import pytest

from bookcraft.infra.crm_freeze import is_crm_frozen, parse_frozen_tenants

TENANT = UUID("14c350e8-d0bc-4ee9-90c7-dea2b7a7a007")
OTHER = UUID("066dd015-2820-47b5-b2fc-1e9704f0f420")


def test_an_empty_list_freezes_nothing() -> None:
    assert parse_frozen_tenants("") == frozenset()
    assert parse_frozen_tenants("[]") == frozenset()
    assert parse_frozen_tenants(None) == frozenset()
    assert is_crm_frozen(TENANT, parse_frozen_tenants("[]")) is False


def test_a_listed_tenant_is_frozen_case_insensitively() -> None:
    frozen = parse_frozen_tenants(f'["{TENANT}"]')
    assert is_crm_frozen(TENANT, frozen) is True
    assert is_crm_frozen(str(TENANT).upper(), frozen) is True
    assert is_crm_frozen(OTHER, frozen) is False


def test_a_turn_without_a_tenant_is_never_frozen() -> None:
    frozen = parse_frozen_tenants(f'["{TENANT}"]')
    assert is_crm_frozen(None, frozen) is False
    assert is_crm_frozen("", frozen) is False


def test_a_malformed_value_fails_closed_rather_than_unfreezing() -> None:
    with pytest.raises(ValueError):
        parse_frozen_tenants("{not json}")
    with pytest.raises(ValueError):
        parse_frozen_tenants('{"tenant": 1}')
    with pytest.raises(ValueError):
        parse_frozen_tenants('[""]')
    with pytest.raises(ValueError):
        parse_frozen_tenants(f'["{TENANT}", "{TENANT}"]')
