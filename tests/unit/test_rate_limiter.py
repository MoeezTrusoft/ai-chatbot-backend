import pytest

from bookcraft.infra.cache import CacheKeyBuilder
from bookcraft.infra.rate_limit import InMemoryRateLimiter, RedisRateLimiter, RedisRateLimitStore


@pytest.mark.asyncio
async def test_redis_rate_limiter_blocks_after_limit() -> None:
    limiter = RedisRateLimiter(
        store=RedisRateLimitStore(FakeRedis()),
        keys=CacheKeyBuilder(environment="test"),
        limit_per_minute=1,
    )

    first = await limiter.check("client-1", scope="unit")
    second = await limiter.check("client-1", scope="unit")

    assert first.allowed is True
    assert second.allowed is False


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_blocks_after_limit() -> None:
    limiter = InMemoryRateLimiter(limit_per_minute=1)

    first = await limiter.check("client-1", scope="unit")
    second = await limiter.check("client-1", scope="unit")

    assert first.allowed is True
    assert second.allowed is False
    assert second.reset_after_seconds >= 1


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_separates_keys() -> None:
    limiter = InMemoryRateLimiter(limit_per_minute=1)

    first = await limiter.check("client-1", scope="unit")
    second = await limiter.check("client-2", scope="unit")

    assert first.allowed is True
    assert second.allowed is True


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, 60)

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        return True

    async def delete(self, key: str) -> int:
        return 1
