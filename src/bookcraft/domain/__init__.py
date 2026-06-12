"""Domain primitives and state models."""

from bookcraft.domain.enums import (
    ContactMethod,
    ManuscriptStatus,
    QueryIntentType,
    SalesStage,
    ServiceCategory,
    Source,
    ToolClass,
    ToolInvocationStatus,
)
from bookcraft.domain.field_registry import (
    FIELD_REGISTRY,
    FieldDef,
    get_forbidden_reasks,
    get_required_for_quote,
)
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState

__all__ = [
    "ContactMethod",
    "FIELD_REGISTRY",
    "FieldDef",
    "FieldMeta",
    "ManuscriptStatus",
    "QueryIntentType",
    "SalesStage",
    "ServiceCategory",
    "Source",
    "ThreadState",
    "ToolClass",
    "ToolInvocationStatus",
    "get_forbidden_reasks",
    "get_required_for_quote",
]
