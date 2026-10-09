from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel


class LLMProvider(Protocol):
    name: str

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        purpose: str,
        system_cache_suffix: str | None = None,
    ) -> BaseModel: ...


class StreamingLLMProvider(LLMProvider, Protocol):
    def stream_text(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        purpose: str,
        system_cache_suffix: str | None = None,
    ) -> AsyncIterator[str]: ...
