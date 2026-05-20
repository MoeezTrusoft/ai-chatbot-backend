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
    ) -> BaseModel: ...
