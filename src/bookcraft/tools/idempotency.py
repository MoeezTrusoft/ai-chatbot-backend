import json
from dataclasses import dataclass

from bookcraft.infra.cache import CacheClient, CacheKeyBuilder


@dataclass(slots=True)
class IdempotencyStore:
    client: CacheClient
    keys: CacheKeyBuilder
    ttl_seconds: int

    async def get(self, idempotency_key: str) -> dict[str, object] | None:
        cached = await self.client.get(self.keys.idempotency(idempotency_key))
        if cached is None:
            return None
        loaded = json.loads(cached)
        if not isinstance(loaded, dict):
            msg = "Idempotency cache value must decode to an object."
            raise ValueError(msg)
        return loaded

    async def store(self, idempotency_key: str, payload: dict[str, object]) -> bool:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return bool(
            await self.client.set(
                self.keys.idempotency(idempotency_key),
                serialized,
                ex=self.ttl_seconds,
                nx=True,
            )
        )


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return int(existed)
