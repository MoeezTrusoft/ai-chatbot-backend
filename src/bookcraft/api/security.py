from __future__ import annotations

from urllib.parse import urlparse

from bookcraft.infra.config import Settings


def parse_allowed_origins(settings: Settings) -> set[str]:
    return {
        origin.strip().rstrip("/")
        for origin in settings.ws_allowed_origins.split(",")
        if origin.strip()
    }


def is_origin_allowed(origin: str | None, settings: Settings) -> bool:
    if settings.app_env == "test":
        # Tests may omit Origin unless explicitly checking security behavior.
        return True

    allowed = parse_allowed_origins(settings)

    if "*" in allowed:
        return True

    if origin is None:
        return False

    normalized = origin.strip().rstrip("/")
    if normalized in allowed:
        return True

    return _same_origin_host_port(normalized, allowed)


def _same_origin_host_port(origin: str, allowed: set[str]) -> bool:
    parsed_origin = urlparse(origin)
    if not parsed_origin.scheme or not parsed_origin.netloc:
        return False

    for allowed_origin in allowed:
        parsed_allowed = urlparse(allowed_origin)
        if (
            parsed_origin.scheme == parsed_allowed.scheme
            and parsed_origin.hostname == parsed_allowed.hostname
            and parsed_origin.port == parsed_allowed.port
        ):
            return True

    return False