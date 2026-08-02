"""In-memory holiday cache for consultation scheduling.

The single source of truth for holiday blackout dates is the
``consultation_holidays`` table (see :mod:`holiday_repository`). This module
holds a tiny, thread-safe in-process cache of those dates so the *synchronous*
scheduling engine — the ``service.py`` normalizer and the ``slots.py`` slot
suggester — can ask "is this a closed day?" with zero DB latency on every
booking / slot decision.

Who keeps the cache fresh (the API process):
  * an initial load on startup and a periodic refresh every
    :data:`HOLIDAY_CACHE_TTL_SECONDS` (the app lifespan task), and
  * a write-through update the moment a holiday is added/removed via the API.

So a holiday added from the CSR calendar takes effect within ~1 minute across
every worker — no code change, no redeploy. When the cache is empty (e.g. unit
tests that never populate it) :func:`is_blackout_date` simply returns ``False``,
so behavior is unchanged unless holidays are actually configured.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import date

# How long a worker may serve a stale holiday set before the lifespan task
# reloads it from the database.
HOLIDAY_CACHE_TTL_SECONDS = 60

_lock = threading.Lock()
_cache: set[str] = set()


def _iso(value: str | date) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def set_holiday_cache(dates: Iterable[str | date]) -> None:
    """Replace the cached holiday set (used by the periodic DB refresh)."""
    normalized = {_iso(d) for d in dates}
    with _lock:
        global _cache
        _cache = normalized


def add_to_cache(value: str | date) -> None:
    """Write-through: reflect a just-added holiday immediately in this process."""
    iso = _iso(value)
    with _lock:
        _cache.add(iso)


def remove_from_cache(value: str | date) -> None:
    """Write-through: reflect a just-removed holiday immediately in this process."""
    iso = _iso(value)
    with _lock:
        _cache.discard(iso)


def cached_holidays() -> set[str]:
    """Return a snapshot copy of the currently cached ISO holiday dates."""
    with _lock:
        return set(_cache)


def is_blackout_date(value: date) -> bool:
    """Return ``True`` if ``value`` (a business-tz calendar date) is a closed day."""
    with _lock:
        return value.isoformat() in _cache
