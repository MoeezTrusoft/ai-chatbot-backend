from __future__ import annotations

from fastapi import Request

from bookcraft.infra.config import Settings


def is_metrics_request_allowed(request: Request, settings: Settings) -> bool:
    if settings.app_env == "test":
        return True

    if settings.metrics_public:
        return True

    if not settings.metrics_bearer_token:
        return False

    authorization = request.headers.get("authorization", "")
    expected = f"Bearer {settings.metrics_bearer_token}"
    return authorization == expected
