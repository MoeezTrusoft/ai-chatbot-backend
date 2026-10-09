"""Conversation execution boundary: one writer per thread and a total time budget."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from bookcraft.components.storage.thread_repository import local_thread_lock
from bookcraft.infra.observability import STAGE_LATENCY

if TYPE_CHECKING:
    from bookcraft.services.chat import ChatService


@dataclass
class TurnWork:
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    flushes: list[Callable[[], Awaitable[None]]] = field(default_factory=list)


_work: ContextVar[TurnWork | None] = ContextVar("turn_work", default=None)


def spawn_turn_task[ResultT](coroutine: Coroutine[Any, Any, ResultT]) -> asyncio.Task[ResultT]:
    work = _work.get()
    if work is None:
        coroutine.close()
        raise RuntimeError("Background work requires a turn execution boundary")
    task = asyncio.create_task(coroutine)
    work.tasks.append(task)
    return task


def register_event_flush(flush: Callable[[], Awaitable[None]]) -> None:
    work = _work.get()
    if work is None:
        raise RuntimeError("Event buffering requires a turn execution boundary")
    work.flushes.append(flush)


def serialized_mutation[ResultT](
    operation: Callable[[ChatService, Any], Awaitable[ResultT]],
) -> Callable[[ChatService, Any], Awaitable[ResultT]]:
    """Apply equally to chat, greetings and externally submitted state changes."""

    @wraps(operation)
    async def execute(self: ChatService, payload: Any) -> ResultT:
        thread_id = payload.thread_id or uuid4()
        if payload.thread_id is None:
            payload = payload.model_copy(update={"thread_id": thread_id})
        repository = self.thread_repository
        lock = repository.serialize(thread_id) if repository else local_thread_lock(thread_id)
        async with asyncio.timeout(self.turn_timeout_seconds):
            waiting_at = time.perf_counter()
            async with lock:
                STAGE_LATENCY.labels(stage="lock_wait").observe(time.perf_counter() - waiting_at)
                work = TurnWork()
                token = _work.set(work)
                try:
                    result = await operation(self, payload)
                    if work.tasks:
                        await asyncio.gather(*work.tasks, return_exceptions=True)
                    for flush in work.flushes:
                        await flush()
                    return result
                finally:
                    # Nothing may keep writing this conversation after its lock is released.
                    for task in work.tasks:
                        if not task.done():
                            task.cancel()
                    if work.tasks:
                        await asyncio.gather(*work.tasks, return_exceptions=True)
                    _work.reset(token)

    return execute
