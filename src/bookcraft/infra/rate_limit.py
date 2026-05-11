from __future__ import annotations

import time
from dataclasses import dataclass, field

from prometheus_client import Counter

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


@dataclass(slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int


@dataclass(slots=True)
class InMemoryRateLimiter:
    limit_per_minute: int
    _buckets: dict[str, list[float]] = field(default_factory=dict)

    def check(self, key: str, *, scope: str) -> RateLimitDecision:
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


def client_ip_from_scope(client_host: str | None) -> str:
    return client_host or "unknown"