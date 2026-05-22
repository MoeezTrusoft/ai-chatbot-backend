from __future__ import annotations

from enum import Enum
from typing import get_args

from bookcraft.components.intent.schemas import IntentVote

__all__ = ["normalize_provider_vote_payload"]


def normalize_provider_vote_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload

    data = dict(payload)

    def as_list(value: object) -> list[object]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, (tuple, set)):
            return list(value)
        return [value]

    def enum_values_for_field(field_name: str) -> set[str]:
        field = IntentVote.model_fields.get(field_name)
        if field is None:
            return set()

        values: set[str] = set()

        def walk(annotation: object) -> None:
            if isinstance(annotation, type) and issubclass(annotation, Enum):
                values.update(str(item.value) for item in annotation)
            for arg in get_args(annotation):
                walk(arg)

        walk(field.annotation)
        return values

    data["query_secondary"] = as_list(data.get("query_secondary"))
    data["service_secondary"] = as_list(data.get("service_secondary"))

    evidence = as_list(data.get("evidence"))
    data["evidence"] = [
        item if isinstance(item, str) else str(item) for item in evidence if item is not None
    ]

    allowed_query = enum_values_for_field("query_primary")
    allowed_service = enum_values_for_field("service_primary")
    allowed_funnel = enum_values_for_field("funnel_stage")

    data["query_secondary"] = [
        str(item)
        for item in data["query_secondary"]
        if isinstance(item, str) and (not allowed_query or item in allowed_query)
    ]

    data["service_secondary"] = [
        str(item)
        for item in data["service_secondary"]
        if isinstance(item, str) and (not allowed_service or item in allowed_service)
    ]

    if allowed_query and data.get("query_primary") not in allowed_query:
        data["query_primary"] = "unclear"

    if allowed_service and data.get("service_primary") not in allowed_service:
        data["service_primary"] = None

    if allowed_funnel and data.get("funnel_stage") not in allowed_funnel:
        data["funnel_stage"] = "new"

    return data
