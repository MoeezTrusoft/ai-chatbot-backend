"""The chatbot's own MCP server surface (C4, S3.6)."""

from bookcraft.components.mcp.server import (
    CHATBOT_MCP_TOOLS,
    CONTRACT_VERSION,
    PERMISSION_MAP,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOL_NAMES,
    TOOLS_BY_NAME,
    ChatbotMcpServer,
    ChatbotTool,
    ToolOutcome,
    idempotency_key_for,
    listed_tools,
)

__all__ = [
    "CHATBOT_MCP_TOOLS",
    "CONTRACT_VERSION",
    "PERMISSION_MAP",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "TOOLS_BY_NAME",
    "TOOL_NAMES",
    "ChatbotMcpServer",
    "ChatbotTool",
    "ToolOutcome",
    "idempotency_key_for",
    "listed_tools",
]
