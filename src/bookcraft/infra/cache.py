from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as redis
from prometheus_client import Counter

from bookcraft.infra.config import Settings

REDIS_CACHE_HITS = Counter("redis_cache_hits_total", "Redis cache hits.")


class CacheClient(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None: ...

    async def delete(self, key: str) -> int: ...


@dataclass(frozen=True, slots=True)
class CacheKeyBuilder:
    environment: str

    def thread_state(self, thread_id: str) -> str:
        return self._key("thread", thread_id, "state")

    def thread_graph(self, thread_id: str) -> str:
        return self._key("thread", thread_id, "graph")

    def idempotency(self, idempotency_key: str) -> str:
        return self._key("idempotency", idempotency_key)

    def embedding(self, language: str, text_hash: str) -> str:
        return self._key("embedding", language, text_hash)

    def trimatch_active_state(self) -> str:
        return self._key("trimatch", "active_state")

    def _key(self, *parts: str) -> str:
        return ":".join(["bc", self.environment, *parts])


def create_redis_client(settings: Settings) -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


@dataclass(slots=True)
class RedisCache:
    client: CacheClient
    keys: CacheKeyBuilder
    default_ttl_seconds: int

    async def get(self, key: str) -> str | None:
        value = await self.client.get(key)
        if value is not None:
            REDIS_CACHE_HITS.inc()
        return value

    async def set_with_ttl(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self.client.set(key, value, ex=ttl_seconds or self.default_ttl_seconds)

    async def set_once(self, key: str, value: str, ttl_seconds: int) -> bool:
        result = await self.client.set(key, value, ex=ttl_seconds, nx=True)
        return bool(result)
