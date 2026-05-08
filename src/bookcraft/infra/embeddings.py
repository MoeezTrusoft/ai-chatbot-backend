from __future__ import annotations

import httpx


class TeiEmbeddingClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/embed",
                json={"inputs": text},
            )
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, list) or not data:
            raise ValueError("TEI returned an empty embedding response.")

        vector = data[0]
        if not isinstance(vector, list):
            raise ValueError("TEI returned an invalid embedding vector.")

        return [float(value) for value in vector]
