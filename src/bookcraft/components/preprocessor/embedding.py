import hashlib
from dataclasses import dataclass

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
    cache: CacheClient | None = None
    keys: CacheKeyBuilder | None = None

    async def embed(self, normalized_text: str, language: str) -> list[float]:
        text_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        cache_key = self.keys.embedding(language, text_hash) if self.keys is not None else None
        if self.cache is not None and cache_key is not None:
            cached = await self.cache.get(cache_key)
            if cached:
                EMBEDDER_CACHE_HITS.inc()
                return [float(part) for part in cached.split(",")]

        try:
            with EMBEDDER_LATENCY.time():
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
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
            structlog.get_logger(__name__).warning("embedding_degraded", error=str(exc))
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
        return [float(item) for item in vector][: self.dimensions]

