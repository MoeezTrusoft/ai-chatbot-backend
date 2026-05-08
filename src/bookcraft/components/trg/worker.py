from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .schemas import GraphUpdateResult


@dataclass(slots=True)
class TRGUpdateWorker:
    max_attempts: int = 3
    retry_delay_seconds: float = 0.01

    async def run(
        self,
        job: Callable[[], Awaitable[GraphUpdateResult]],
    ) -> GraphUpdateResult:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return await job()
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(self.retry_delay_seconds)
        if last_error is None:
            msg = "TRG worker has no attempts configured"
            raise RuntimeError(msg)
        raise last_error
