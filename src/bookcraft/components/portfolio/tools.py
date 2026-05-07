from __future__ import annotations

from bookcraft.domain.enums import ToolClass
from bookcraft.tools.dispatcher import RetryPolicy, ToolDefinition, ToolRegistry
from bookcraft.tools.schemas import ToolContext

from .engine import PortfolioEngine
from .schemas import PortfolioRequest, PortfolioResponse


def register_portfolio_tools(registry: ToolRegistry, engine: PortfolioEngine) -> None:
    async def request_handler(
        request: PortfolioRequest,
        context: ToolContext,
    ) -> PortfolioResponse:
        del context
        return engine.request_samples(request)

    registry.register(
        ToolDefinition(
            name="portfolio.request_samples.v1",
            tool_class=ToolClass.READ,
            input_model=PortfolioRequest,
            output_model=PortfolioResponse,
            handler=request_handler,
            timeout_seconds=2.0,
            retry_policy=RetryPolicy(attempts=1),
        )
    )
