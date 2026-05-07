from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from bookcraft.domain.enums import ToolClass, ToolInvocationStatus
from bookcraft.tools.gating import ToolGatingPolicy
from bookcraft.tools.idempotency import IdempotencyStore
from bookcraft.tools.schemas import ToolContext, ToolResultEnvelope

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class ToolError(Exception):
    pass


class ToolNotFoundError(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class ToolDefinition(Generic[InputT, OutputT]):  # noqa: UP046 - TypeVar improves Pydantic typing.
    name: str
    tool_class: ToolClass
    input_model: type[InputT]
    output_model: type[OutputT]
    handler: Callable[[InputT, ToolContext], Awaitable[OutputT]]
    timeout_seconds: float = 5.0
    retry_policy: RetryPolicy = RetryPolicy()
    circuit_breaker_enabled: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition[Any, Any]] = {}

    def register(self, definition: ToolDefinition[Any, Any]) -> None:
        if definition.name in self._tools:
            msg = f"Tool already registered: {definition.name}"
            raise ValueError(msg)
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition[Any, Any]:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc


class AuditSink:
    async def record(
        self,
        *,
        context: ToolContext,
        tool_name: str,
        params_hash: str,
        params: dict[str, object],
        status: ToolInvocationStatus,
        result: dict[str, object] | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        raise NotImplementedError


class MemoryAuditSink(AuditSink):
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(
        self,
        *,
        context: ToolContext,
        tool_name: str,
        params_hash: str,
        params: dict[str, object],
        status: ToolInvocationStatus,
        result: dict[str, object] | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.records.append(
            {
                "context": context.model_dump(mode="json"),
                "tool_name": tool_name,
                "params_hash": params_hash,
                "params": params,
                "status": status.value,
                "result": result,
                "error": error,
                "duration_ms": duration_ms,
            }
        )


@dataclass(slots=True)
class ToolDispatcher:
    registry: ToolRegistry
    idempotency_store: IdempotencyStore
    audit_sink: AuditSink
    gating_policy: ToolGatingPolicy = ToolGatingPolicy()
    circuit_breaker_threshold: int = 3
    _failure_counts: dict[str, int] | None = None

    async def invoke(
        self,
        *,
        tool_name: str,
        raw_input: dict[str, object],
        context: ToolContext,
    ) -> ToolResultEnvelope:
        definition = self.registry.get(tool_name)
        failure_counts = self._get_failure_counts()
        if definition.circuit_breaker_enabled and failure_counts.get(tool_name, 0) >= (
            self.circuit_breaker_threshold
        ):
            msg = f"Circuit breaker is open for {tool_name}"
            raise ToolError(msg)

        cached = await self.idempotency_store.get(context.idempotency_key)
        if cached is not None:
            return ToolResultEnvelope(
                tool_name=tool_name,
                status=ToolInvocationStatus.IDEMPOTENT_REPLAY.value,
                result=cached,
                replayed=True,
            )

        started = time.perf_counter()
        params_hash = _hash_params(raw_input)
        gating_decision = self.gating_policy.decide(
            tool_name=tool_name,
            tool_class=definition.tool_class,
        )
        if gating_decision.deferred:
            await self.audit_sink.record(
                context=context,
                tool_name=tool_name,
                params_hash=params_hash,
                params=raw_input,
                status=ToolInvocationStatus.DEFERRED,
                result={"reason": gating_decision.reason},
                duration_ms=_duration_ms(started),
            )
            return ToolResultEnvelope(
                tool_name=tool_name,
                status=ToolInvocationStatus.DEFERRED.value,
                result={"reason": gating_decision.reason},
            )
        if not gating_decision.allowed:
            msg = gating_decision.reason or f"Tool blocked by gating policy: {tool_name}"
            raise ToolError(msg)

        try:
            validated_input = definition.input_model.model_validate(raw_input)
        except ValidationError as exc:
            await self.audit_sink.record(
                context=context,
                tool_name=tool_name,
                params_hash=params_hash,
                params=raw_input,
                status=ToolInvocationStatus.FAILED,
                error=str(exc),
                duration_ms=_duration_ms(started),
            )
            raise ToolValidationError(str(exc)) from exc

        try:
            output = await self._run_with_retry(definition, validated_input, context)
            validated_output = definition.output_model.model_validate(output)
            result = validated_output.model_dump(mode="json")
            await self.idempotency_store.store(context.idempotency_key, result)
            failure_counts[tool_name] = 0
            await self.audit_sink.record(
                context=context,
                tool_name=tool_name,
                params_hash=params_hash,
                params=raw_input,
                status=ToolInvocationStatus.SUCCEEDED,
                result=result,
                duration_ms=_duration_ms(started),
            )
            return ToolResultEnvelope(
                tool_name=tool_name,
                status=ToolInvocationStatus.SUCCEEDED.value,
                result=result,
            )
        except Exception as exc:
            failure_counts[tool_name] = failure_counts.get(tool_name, 0) + 1
            structlog.get_logger(__name__).warning(
                "tool_invocation_failed",
                tool_name=tool_name,
                error=str(exc),
            )
            await self.audit_sink.record(
                context=context,
                tool_name=tool_name,
                params_hash=params_hash,
                params=raw_input,
                status=ToolInvocationStatus.FAILED,
                error=str(exc),
                duration_ms=_duration_ms(started),
            )
            raise

    async def _run_with_retry(
        self,
        definition: ToolDefinition[InputT, OutputT],
        validated_input: InputT,
        context: ToolContext,
    ) -> OutputT:
        last_error: Exception | None = None
        for _ in range(definition.retry_policy.attempts):
            try:
                return await asyncio.wait_for(
                    definition.handler(validated_input, context),
                    timeout=definition.timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
        if last_error is None:
            msg = f"Tool {definition.name} has no retry attempts configured."
            raise ToolError(msg)
        raise last_error

    def _get_failure_counts(self) -> dict[str, int]:
        if self._failure_counts is None:
            self._failure_counts = {}
        return self._failure_counts


def _hash_params(params: dict[str, object]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _duration_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
