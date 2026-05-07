from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from bookcraft.domain.enums import ToolClass
from bookcraft.infra.cache import CacheKeyBuilder
from bookcraft.tools import (
    IdempotencyStore,
    MemoryAuditSink,
    MemoryCache,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
    ToolValidationError,
)
from bookcraft.tools.gating import ToolGatingPolicy


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class EchoOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    echoed: str


async def echo_handler(input_data: EchoInput, context: ToolContext) -> EchoOutput:
    del context
    return EchoOutput(echoed=input_data.value)


def build_dispatcher() -> tuple[ToolDispatcher, MemoryAuditSink]:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo.v1",
            tool_class=ToolClass.READ,
            input_model=EchoInput,
            output_model=EchoOutput,
            handler=echo_handler,
        )
    )
    audit_sink = MemoryAuditSink()
    idempotency = IdempotencyStore(
        client=MemoryCache(),
        keys=CacheKeyBuilder(environment="test"),
        ttl_seconds=60,
    )
    return ToolDispatcher(registry, idempotency, audit_sink), audit_sink


def tool_context(idempotency_key: str = "idem-1") -> ToolContext:
    return ToolContext(
        thread_id=uuid4(),
        customer_id=None,
        turn_sequence=1,
        invoked_by="test",
        correlation_id="corr-1",
        idempotency_key=idempotency_key,
        environment="test",
    )


@pytest.mark.asyncio
async def test_dispatcher_validates_and_invokes_tool() -> None:
    dispatcher, audit_sink = build_dispatcher()

    result = await dispatcher.invoke(
        tool_name="echo.v1",
        raw_input={"value": "hello"},
        context=tool_context(),
    )

    assert result.result == {"echoed": "hello"}
    assert result.replayed is False
    assert audit_sink.records[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_dispatcher_replays_duplicate_idempotency_key() -> None:
    dispatcher, audit_sink = build_dispatcher()
    context = tool_context()

    first = await dispatcher.invoke(
        tool_name="echo.v1",
        raw_input={"value": "hello"},
        context=context,
    )
    second = await dispatcher.invoke(
        tool_name="echo.v1",
        raw_input={"value": "changed"},
        context=context,
    )

    assert first.result == second.result
    assert second.replayed is True
    assert len(audit_sink.records) == 1


@pytest.mark.asyncio
async def test_dispatcher_rejects_invalid_input_schema() -> None:
    dispatcher, audit_sink = build_dispatcher()

    with pytest.raises(ToolValidationError):
        await dispatcher.invoke(
            tool_name="echo.v1",
            raw_input={"value": "hello", "extra": "blocked"},
            context=tool_context(),
        )

    assert audit_sink.records[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_high_stakes_document_tool_defers_in_manual_mode() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="generate_nda.v1",
            tool_class=ToolClass.HIGH_STAKES_DOCUMENT,
            input_model=EchoInput,
            output_model=EchoOutput,
            handler=echo_handler,
        )
    )
    audit_sink = MemoryAuditSink()
    dispatcher = ToolDispatcher(
        registry=registry,
        idempotency_store=IdempotencyStore(
            client=MemoryCache(),
            keys=CacheKeyBuilder(environment="test"),
            ttl_seconds=60,
        ),
        audit_sink=audit_sink,
        gating_policy=ToolGatingPolicy(nda_mode="manual", agreement_mode="manual"),
    )

    result = await dispatcher.invoke(
        tool_name="generate_nda.v1",
        raw_input={"value": "hello"},
        context=tool_context(),
    )

    assert result.status == "deferred"
    assert audit_sink.records[0]["status"] == "deferred"
