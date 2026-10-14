"""Frozen-tenant CRM write gate (S12.2.2).

D1's single-writer rule: once a tenant is cut over, **Sales** owns its CRM. The
bot's local ``sales_leads`` / ``sales_consultations`` tables stay readable for the
history and for standalone deployments, but a *write* for a cut-over tenant would
recreate exactly the dual-CRM divergence the integration exists to remove — and
it would do it silently, on the degraded path, where nobody is watching.

So the tenants whose CRM Sales owns are listed in config
(``CHATBOT_FROZEN_CRM_TENANTS_JSON``) and the dispatcher refuses the local write
for them instead of writing. A frozen write is not "lost": the caller reports the
turn as degraded, and Sales remains the system of record.

An EMPTY list (the default) freezes nothing, so dev/standalone behaviour — where
the local tables ARE the CRM — is unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class CrmFrozenError(RuntimeError):
    """A local CRM write was refused because Sales owns this tenant's CRM."""


def parse_frozen_tenants(raw: str | None) -> frozenset[str]:
    """Parse ``CHATBOT_FROZEN_CRM_TENANTS_JSON`` into lowercased tenant ids.

    A malformed entry is fatal rather than skipped: silently dropping a tenant
    would silently re-enable the local writes the freeze exists to stop.
    """
    text = (raw or "").strip()
    if not text:
        return frozenset()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("CHATBOT_FROZEN_CRM_TENANTS_JSON is not valid JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("CHATBOT_FROZEN_CRM_TENANTS_JSON must be a JSON array of tenant ids")
    tenants: set[str] = set()
    for entry in parsed:
        value = str(entry or "").strip().lower()
        if not value:
            raise ValueError("CHATBOT_FROZEN_CRM_TENANTS_JSON entries must be tenant ids")
        if value in tenants:
            raise ValueError(f"duplicate tenant in CHATBOT_FROZEN_CRM_TENANTS_JSON: {value}")
        tenants.add(value)
    return frozenset(tenants)


def is_crm_frozen(
    tenant_id: UUID | str | None, frozen: Iterable[str] | None
) -> bool:
    """Whether the local CRM tables are read-only for this tenant (S12.2.2).

    A turn with no tenant is never frozen: it cannot have been cut over.
    """
    wanted = str(tenant_id or "").strip().lower()
    if not wanted:
        return False
    return wanted in {str(value).strip().lower() for value in (frozen or ())}


__all__ = ["CrmFrozenError", "is_crm_frozen", "parse_frozen_tenants"]
