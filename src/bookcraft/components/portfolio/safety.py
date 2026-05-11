from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

APPROVED_PORTFOLIO_EXACT_HOSTS = {
    "pub-511d184d6c4a4ad1b53c7fdf29e12e40.r2.dev",
}

APPROVED_PORTFOLIO_HOST_SUFFIXES = (
    "amazon.com",
    "amazon.co.uk",
    "amazon.ca",
    "amazon.com.au",
    "bookcraftpublishers.com",
    "youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
    "vimeo.com",
    "drive.google.com",
    "docs.google.com",
    "storage.googleapis.com",
)

BLOCKED_PORTFOLIO_HOSTS = {
    "localhost",
    "127.0.0.1",
}


def is_safe_portfolio_url(value: str | None) -> bool:
    if value is None:
        return True

    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False

    if host in BLOCKED_PORTFOLIO_HOSTS:
        return False

    if _is_private_ip(host):
        return False

    if _looks_internal(host):
        return False

    if host in APPROVED_PORTFOLIO_EXACT_HOSTS:
        return True

    return any(
        host == suffix or host.endswith(f".{suffix}") for suffix in APPROVED_PORTFOLIO_HOST_SUFFIXES
    )


def unsafe_portfolio_url_reason(value: str | None) -> str | None:
    if value is None:
        return None
    if is_safe_portfolio_url(value):
        return None
    return "portfolio link host is not on the approved public allowlist"


def _is_private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _looks_internal(host: str) -> bool:
    internal_fragments = (
        ".local",
        ".internal",
        ".lan",
        ".corp",
        ".test",
        "staging",
        "dev.",
        "admin",
        "private",
        "intranet",
    )
    return any(fragment in host for fragment in internal_fragments)
