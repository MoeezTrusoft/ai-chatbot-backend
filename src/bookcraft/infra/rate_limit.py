from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from prometheus_client import Counter

from bookcraft.infra.cache import CacheKeyBuilder

_logger = structlog.get_logger(__name__)

RATE_LIMIT_ALLOWED = Counter(
    "rate_limit_allowed_total",
    "Allowed requests after rate-limit check.",
    ["scope"],
)

RATE_LIMIT_BLOCKED = Counter(
    "rate_limit_blocked_total",
    "Blocked requests after rate-limit check.",
    ["scope"],
)

RATE_LIMIT_FAIL_OPEN = Counter(
    "rate_limit_fail_open_total",
    "Requests allowed because the rate-limit store was unreachable.",
    ["scope"],
)


@dataclass(slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int


class RateLimiter(Protocol):
    async def check(self, key: str, *, scope: str) -> RateLimitDecision: ...


@dataclass(slots=True)
class InMemoryRateLimiter:
    limit_per_minute: int
    _buckets: dict[str, list[float]] = field(default_factory=dict)

    async def check(self, key: str, *, scope: str) -> RateLimitDecision:
        now = time.monotonic()
        window_start = now - 60
        hits = [hit for hit in self._buckets.get(key, []) if hit >= window_start]

        if len(hits) >= self.limit_per_minute:
            oldest = min(hits) if hits else now
            reset_after = max(1, round(60 - (now - oldest)))
            self._buckets[key] = hits
            RATE_LIMIT_BLOCKED.labels(scope=scope).inc()
            return RateLimitDecision(
                allowed=False,
                limit=self.limit_per_minute,
                remaining=0,
                reset_after_seconds=reset_after,
            )

        hits.append(now)
        self._buckets[key] = hits
        RATE_LIMIT_ALLOWED.labels(scope=scope).inc()
        return RateLimitDecision(
            allowed=True,
            limit=self.limit_per_minute,
            remaining=max(0, self.limit_per_minute - len(hits)),
            reset_after_seconds=60,
        )


@dataclass(slots=True)
class RedisRateLimitStore:
    client: Any

    async def incr(self, key: str) -> int:
        return int(await self.client.incr(key))

    async def expire(self, key: str, seconds: int) -> bool:
        return bool(await self.client.expire(key, seconds))

    async def ttl(self, key: str) -> int:
        return int(await self.client.ttl(key))


@dataclass(slots=True)
class RedisRateLimiter:
    store: RedisRateLimitStore
    keys: CacheKeyBuilder
    limit_per_minute: int

    async def check(self, key: str, *, scope: str) -> RateLimitDecision:
        redis_key = self.keys._key("rate_limit", key)
        try:
            count = await self.store.incr(redis_key)

            if count == 1:
                await self.store.expire(redis_key, 60)

            ttl = await self.store.ttl(redis_key)
        except Exception as exc:  # noqa: BLE001 - fail open on any store failure
            # The rate-limit store (Redis) is unreachable or errored. A rate
            # limiter must never be the reason a customer message is dropped, so
            # fail OPEN: allow the request rather than 500 the whole chat turn.
            RATE_LIMIT_FAIL_OPEN.labels(scope=scope).inc()
            _logger.warning(
                "rate_limit_store_unavailable_fail_open",
                scope=scope,
                error=str(exc),
                exc_class=type(exc).__name__,
            )
            return RateLimitDecision(
                allowed=True,
                limit=self.limit_per_minute,
                remaining=self.limit_per_minute,
                reset_after_seconds=60,
            )

        reset_after = max(1, ttl if ttl > 0 else 60)

        if count > self.limit_per_minute:
            RATE_LIMIT_BLOCKED.labels(scope=scope).inc()
            return RateLimitDecision(
                allowed=False,
                limit=self.limit_per_minute,
                remaining=0,
                reset_after_seconds=reset_after,
            )

        RATE_LIMIT_ALLOWED.labels(scope=scope).inc()
        return RateLimitDecision(
            allowed=True,
            limit=self.limit_per_minute,
            remaining=max(0, self.limit_per_minute - count),
            reset_after_seconds=reset_after,
        )


def client_ip_from_scope(client_host: str | None) -> str:
    return client_host or "unknown"
