"""The chatbot's own MCP server (C4, S3.6).

A thin adapter: every tool routes to the capability the frozen contract names
(`erp-contracts/src/mcp/chatbot.js`), so nothing here re-implements pricing,
portfolio, document or conversation logic. Mounted only when
``CHATBOT_MCP_SERVER_ENABLED=true`` (S3.6.4) — an off deployment has no extra
route and behaves exactly as before.

The tool map and the permission gate are mirrored from that contract and checked
by ``tests/unit/test_chatbot_mcp_contract_parity.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID

import structlog

from bookcraft.infra.service_tokens import ServicePrincipal

_logger = structlog.get_logger(__name__)

# ── Wire constants (mirror of erp-contracts/src/mcp/chatbot.js) ──────────────
CONTRACT_VERSION: Final = "chatbot-mcp@1"
PROTOCOL_VERSION: Final = "2025-06-18"
SERVER_NAME: Final = "bookcraft-chatbot"
SERVER_VERSION: Final = "1.0.0"
AUDIENCE: Final = "internal"

JSONRPC_VERSION: Final = "2.0"
METHOD_INITIALIZE: Final = "initialize"
METHOD_LIST_TOOLS: Final = "tools/list"
METHOD_CALL_TOOL: Final = "tools/call"

RPC_INVALID_REQUEST: Final = -32600
RPC_METHOD_NOT_FOUND: Final = -32601
RPC_INVALID_PARAMS: Final = -32602
RPC_INTERNAL_ERROR: Final = -32603
RPC_DOMAIN_ERROR: Final = -32000
RPC_AUTH_ERROR: Final = -32001

ERROR_AUTH: Final = "CHATBOT-0401"
ERROR_FORBIDDEN: Final = "CHATBOT-0403"
ERROR_NOT_FOUND: Final = "CHATBOT-0404"
ERROR_INVALID_ARGS: Final = "CHATBOT-0422"
ERROR_INTERNAL: Final = "CHATBOT-0500"

ERROR_RPC_CODES: Final[dict[str, int]] = {
    ERROR_AUTH: RPC_AUTH_ERROR,
    ERROR_FORBIDDEN: RPC_DOMAIN_ERROR,
    ERROR_NOT_FOUND: RPC_DOMAIN_ERROR,
    ERROR_INVALID_ARGS: RPC_INVALID_PARAMS,
    ERROR_INTERNAL: RPC_INTERNAL_ERROR,
}
RETRYABLE_ERRORS: Final[frozenset[str]] = frozenset({ERROR_INTERNAL})

@dataclass(frozen=True, slots=True)
class ChatbotTool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    permission: str
    rate_class: Literal["read", "write"]
    target_kind: Literal[
        "chat_service", "tool_registry", "tool_registry_by_arg", "thread_repository"
    ]
    # For `chat_service`: the service operation. For `tool_registry`: the tool.
    # For `tool_registry_by_arg`: the `argument=value:tool` encoding is kept in
    # `target_map` instead.
    target: str
    target_argument: str | None = None
    target_map: Mapping[str, str] = field(default_factory=dict)
    required_arguments: tuple[str, ...] = ()

    def listed(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


_UUID = {"type": "string", "format": "uuid"}
_STRING = {"type": "string"}


def _pass_through() -> dict[str, Any]:
    """The registry tool owns its input model; only `thread_id` is pinned here."""
    return {
        "type": "object",
        "properties": {"thread_id": _UUID},
        "required": ["thread_id"],
        "additionalProperties": True,
    }


def _object(
    properties: dict[str, Any] | None = None, required: tuple[str, ...] = ()
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = list(required)
    return schema


CHATBOT_MCP_TOOLS: Final[tuple[ChatbotTool, ...]] = (
    ChatbotTool(
        name="chat.turn",
        title="Run a chat turn",
        description=(
            "Advance a conversation by one visitor message through the governed chat "
            "pipeline and return the reply bubbles."
        ),
        input_schema=_object(
            {
                "thread_id": _UUID,
                "customer_id": _UUID,
                "message": _STRING,
                "correlation_id": _STRING,
                "landing_page": _STRING,
            },
            ("thread_id", "message"),
        ),
        permission="chat.conversation.write",
        rate_class="write",
        target_kind="chat_service",
        target="handle_turn",
        required_arguments=("thread_id", "message"),
    ),
    ChatbotTool(
        name="chat.greet",
        title="Generate the opening message",
        description=(
            "Produce the proactive first message for a visitor opening the widget. "
            "Read-only: nothing is persisted."
        ),
        input_schema=_object(
            {
                "thread_id": _UUID,
                "customer_id": _UUID,
                "landing_page": _STRING,
                "landing_keyword": _STRING,
            }
        ),
        permission="chat.greeting.read",
        rate_class="read",
        target_kind="chat_service",
        target="handle_greet",
    ),
    ChatbotTool(
        name="chat.state",
        title="Read conversation state",
        description=(
            "Read the persisted thread state for a conversation (stage, captured "
            "contact, consultation progress)."
        ),
        input_schema=_object({"thread_id": _UUID}, ("thread_id",)),
        permission="chat.state.read",
        rate_class="read",
        target_kind="thread_repository",
        target="load",
        required_arguments=("thread_id",),
    ),
    ChatbotTool(
        name="pricing.quote",
        title="Quote a project",
        description=(
            "Price a project through the in-process pricing tool (the same engine a "
            "chat turn uses), returning the quote and its assumptions."
        ),
        input_schema=_pass_through(),
        permission="pricing.quote.get",
        rate_class="read",
        target_kind="tool_registry",
        target="pricing.quote.estimate.v2",
        required_arguments=("thread_id",),
    ),
    ChatbotTool(
        name="portfolio.lookup",
        title="Look up portfolio samples",
        description="Retrieve portfolio samples for a genre through the in-process portfolio tool.",
        input_schema=_pass_through(),
        permission="portfolio.sample.list",
        rate_class="read",
        target_kind="tool_registry",
        target="portfolio.request_samples.v1",
        required_arguments=("thread_id",),
    ),
    ChatbotTool(
        name="documents.render",
        title="Render a document",
        description=(
            "Generate an NDA or agreement through the in-process document tools. "
            "`kind` selects the document."
        ),
        input_schema=_object(
            {
                "kind": {"type": "string", "enum": ["nda", "agreement"]},
                "thread_id": _UUID,
                "customer_id": _UUID,
            },
            ("kind", "thread_id"),
        ),
        permission="documents.document.export",
        rate_class="write",
        target_kind="tool_registry_by_arg",
        target="kind",
        target_argument="kind",
        target_map={
            "nda": "documents.generate_nda.v1",
            "agreement": "documents.generate_agreement.v1",
        },
        required_arguments=("kind", "thread_id"),
    ),
)

TOOLS_BY_NAME: Final[dict[str, ChatbotTool]] = {tool.name: tool for tool in CHATBOT_MCP_TOOLS}
TOOL_NAMES: Final[tuple[str, ...]] = tuple(tool.name for tool in CHATBOT_MCP_TOOLS)
PERMISSION_MAP: Final[dict[str, str]] = {tool.name: tool.permission for tool in CHATBOT_MCP_TOOLS}


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """What a tool call produced, in the terms the MCP response needs."""

    ok: bool
    data: dict[str, Any] | None = None
    code: str | None = None
    message: str = ""

    @staticmethod
    def success(data: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(ok=True, data=data)

    @staticmethod
    def failure(code: str, message: str) -> ToolOutcome:
        return ToolOutcome(ok=False, code=code, message=message)


# The server's collaborators, each already owned elsewhere in the app.
ChatTurnHandler = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
ToolInvoker = Callable[
    [str, dict[str, Any], ServicePrincipal], Awaitable[Mapping[str, Any]]
]
ThreadLoader = Callable[[UUID], Awaitable[Mapping[str, Any]]]


class ChatbotMcpServer:
    """Serves `initialize`, `tools/list` and `tools/call` for the bot's tools."""

    def __init__(
        self,
        *,
        handle_turn: ChatTurnHandler | None = None,
        handle_greet: ChatTurnHandler | None = None,
        invoke_tool: ToolInvoker | None = None,
        load_thread_state: ThreadLoader | None = None,
    ) -> None:
        # Each collaborator is already owned elsewhere in the app; the server only
        # routes to it (S3.6.2).
        self._handle_turn = handle_turn
        self._handle_greet = handle_greet
        self._invoke_tool = invoke_tool
        self._load_thread_state = load_thread_state

    # ── Protocol ────────────────────────────────────────────────────────────
    async def handle_rpc(
        self, message: Mapping[str, Any], principal: ServicePrincipal | None
    ) -> dict[str, Any]:
        """Handle one JSON-RPC message and return the response envelope."""
        call_id = message.get("id")
        method = message.get("method")
        if message.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
            return _error_response(call_id, RPC_INVALID_REQUEST, "Invalid request")

        if method == METHOD_INITIALIZE:
            return _result_response(call_id, self._initialize(message.get("params")))
        if method == METHOD_LIST_TOOLS:
            return _result_response(call_id, {"tools": listed_tools()})
        if method == METHOD_CALL_TOOL:
            if principal is None:
                return _domain_error(call_id, ERROR_AUTH, "A verified service token is required")
            return await self._call_tool(call_id, message.get("params"), principal)
        return _error_response(call_id, RPC_METHOD_NOT_FOUND, f"Unknown method: {method}")

    @staticmethod
    def _initialize(params: object) -> dict[str, Any]:
        requested = ""
        if isinstance(params, Mapping):
            requested = str(params.get("protocolVersion") or "")
        if requested and requested != PROTOCOL_VERSION:
            # Answer with the pinned revision: the client decides whether to
            # proceed, and a bot deployment never negotiates down silently.
            _logger.info("chatbot_mcp_protocol_mismatch", requested=requested)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        }

    async def _call_tool(
        self, call_id: object, params: object, principal: ServicePrincipal
    ) -> dict[str, Any]:
        if not isinstance(params, Mapping):
            return _domain_error(call_id, ERROR_INVALID_ARGS, "params must be an object")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            return _domain_error(call_id, ERROR_INVALID_ARGS, "name and arguments are required")

        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return _domain_error(call_id, ERROR_NOT_FOUND, f"Unknown tool: {name}")
        if tool.permission not in principal.scopes:
            # The permission gate is per call: a caller sees the manifest but can
            # only invoke what its scope covers.
            _logger.warning(
                "chatbot_mcp_permission_denied",
                tool=tool.name,
                subject=principal.subject,
                permission=tool.permission,
            )
            return _domain_error(call_id, ERROR_FORBIDDEN, f"Missing permission {tool.permission}")

        missing = [
            key
            for key in tool.required_arguments
            if not str(arguments.get(key) or "").strip()
        ]
        if missing:
            return _domain_error(
                call_id, ERROR_INVALID_ARGS, f"Missing required arguments: {', '.join(missing)}"
            )

        outcome = await self._dispatch(tool, dict(arguments), principal)
        if not outcome.ok:
            return _domain_error(call_id, outcome.code or ERROR_INTERNAL, outcome.message)
        return _result_response(
            call_id, {"content": [{"type": "text", "text": json.dumps(outcome.data or {})}]}
        )

    async def _dispatch(
        self, tool: ChatbotTool, arguments: dict[str, Any], principal: ServicePrincipal
    ) -> ToolOutcome:
        try:
            if tool.target_kind == "chat_service":
                handler = (
                    self._handle_turn if tool.target == "handle_turn" else self._handle_greet
                )
                if handler is None:
                    return ToolOutcome.failure(ERROR_INTERNAL, "chat service is not available")
                return ToolOutcome.success(dict(await handler(arguments)))
            if tool.target_kind == "thread_repository":
                if self._load_thread_state is None:
                    return ToolOutcome.failure(ERROR_INTERNAL, "thread repository is not available")
                thread_id = _uuid_or_none(arguments.get("thread_id"))
                if thread_id is None:
                    return ToolOutcome.failure(ERROR_INVALID_ARGS, "thread_id must be a UUID")
                return ToolOutcome.success(dict(await self._load_thread_state(thread_id)))
            if tool.target_kind == "tool_registry":
                return await self._invoke(tool.target, arguments, principal)
            mapped = tool.target_map.get(str(arguments.get(tool.target_argument or "")))
            if not mapped:
                return ToolOutcome.failure(
                    ERROR_INVALID_ARGS,
                    f"{tool.target_argument} must be one of: {', '.join(sorted(tool.target_map))}",
                )
            return await self._invoke(mapped, arguments, principal)
        except Exception as exc:  # noqa: BLE001 - one caller's failure is not the server's
            _logger.error(
                "chatbot_mcp_tool_failed",
                tool=tool.name,
                exc_class=exc.__class__.__name__,
                detail=str(exc)[:300],
            )
            return ToolOutcome.failure(ERROR_INTERNAL, f"{exc.__class__.__name__}")

    async def _invoke(
        self, tool_name: str, arguments: dict[str, Any], principal: ServicePrincipal
    ) -> ToolOutcome:
        if self._invoke_tool is None:
            return ToolOutcome.failure(ERROR_INTERNAL, "tool registry is not available")
        result = await self._invoke_tool(tool_name, arguments, principal)
        return ToolOutcome.success(dict(result))


def _uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _result_response(call_id: object, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": call_id, "result": dict(result)}


def _error_response(call_id: object, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": call_id,
        "error": {"code": code, "message": message},
    }


def _domain_error(call_id: object, domain_code: str, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": call_id,
        "error": {
            "code": ERROR_RPC_CODES.get(domain_code, RPC_DOMAIN_ERROR),
            "message": message,
            "data": {"code": domain_code, "retryable": domain_code in RETRYABLE_ERRORS},
        },
    }


def listed_tools() -> list[dict[str, Any]]:
    """The exact `tools/list` payload — identical for every caller (S3.6.3)."""
    return [tool.listed() for tool in CHATBOT_MCP_TOOLS]


def idempotency_key_for(tool: str, arguments: Mapping[str, Any], *, principal: str) -> str:
    """Stable per (caller, tool, arguments), so a retried call replays rather than repeats."""
    payload = json.dumps(dict(arguments), sort_keys=True, default=str)
    digest = hashlib.sha256(f"{principal}:{tool}:{payload}".encode()).hexdigest()
    return digest[:32]
