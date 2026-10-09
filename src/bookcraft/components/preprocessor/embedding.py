import asyncio
import hashlib
import math
from dataclasses import dataclass, field

import httpx
import structlog
from prometheus_client import Counter, Histogram

from bookcraft.infra.cache import CacheClient, CacheKeyBuilder

EMBEDDER_LATENCY = Histogram("embedder_latency_seconds", "TEI embedding request latency.")
EMBEDDER_CACHE_HITS = Counter("embedder_cache_hit_total", "Embedding cache hits.")


@dataclass(slots=True)
class EmbeddingClient:
    tei_url: str
    timeout_seconds: float
    dimensions: int
    degraded_mode_enabled: bool
    offline: bool = False
    cache: CacheClient | None = None
    keys: CacheKeyBuilder | None = None
    model_revision: str = "default"
    preprocessing_version: str = "1"
    client: httpx.AsyncClient | None = None
    _pending: dict[str, asyncio.Task[list[float]]] = field(default_factory=dict, init=False)

    async def aclose(self) -> None:
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def embed(self, normalized_text: str, language: str) -> list[float]:
        if self.offline:
            return [0.0] * self.dimensions
        text_hash = self.cache_digest(normalized_text, language)
        task = self._pending.get(text_hash)
        if task is None:
            task = asyncio.create_task(self._embed(normalized_text, language, text_hash))
            self._pending[text_hash] = task

            def finished(completed: asyncio.Task[list[float]]) -> None:
                self._pending.pop(text_hash, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        # One disconnected caller must not cancel work shared with another caller.
        return list(await asyncio.shield(task))

    def cache_digest(self, normalized_text: str, language: str) -> str:
        identity = (
            self.tei_url,
            self.model_revision,
            str(self.dimensions),
            self.preprocessing_version,
            language,
            normalized_text,
        )
        return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()

    async def _embed(self, normalized_text: str, language: str, text_hash: str) -> list[float]:
        cache_key = self.keys.embedding(language, text_hash) if self.keys is not None else None
        if self.cache is not None and cache_key is not None:
            cached = await self.cache.get(cache_key)
            if cached:
                try:
                    vector = self._validate_vector([float(part) for part in cached.split(",")])
                except ValueError:
                    await self.cache.delete(cache_key)
                else:
                    EMBEDDER_CACHE_HITS.inc()
                    return vector

        try:
            with EMBEDDER_LATENCY.time():
                if self.client is None:
                    self.client = httpx.AsyncClient(
                        timeout=self.timeout_seconds,
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                    )
                response = await self.client.post(
                    f"{self.tei_url}/embed",
                    json={"inputs": [normalized_text]},
                )
                response.raise_for_status()
                payload = response.json()
            vector = self._parse_vector(payload)
            if self.cache is not None and cache_key is not None:
                await self.cache.set(cache_key, ",".join(str(item) for item in vector), ex=86400)
            return vector
        except Exception as exc:
            structlog.get_logger(__name__).warning("embedding_degraded", error=type(exc).__name__)
            if not self.degraded_mode_enabled:
                raise
            return [0.0] * self.dimensions

    def _parse_vector(self, payload: object) -> list[float]:
        vector: object
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            vector = payload[0]
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            first = payload["data"][0]
            vector = first.get("embedding") if isinstance(first, dict) else first
        else:
            msg = "Unsupported TEI embedding response."
            raise ValueError(msg)
        if not isinstance(vector, list):
            msg = "TEI embedding vector missing."
            raise ValueError(msg)
        return self._validate_vector([float(item) for item in vector])

    def _validate_vector(self, vector: list[float]) -> list[float]:
        if len(vector) != self.dimensions or not all(math.isfinite(item) for item in vector):
            raise ValueError("Embedding has invalid dimensions or non-finite values")
        return vector
