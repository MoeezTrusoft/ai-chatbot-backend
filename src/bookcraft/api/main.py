from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import sentry_sdk
import structlog
from fastapi import FastAPI, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sentry_sdk.integrations.fastapi import FastApiIntegration

from bookcraft.infra.config import Settings, get_settings
from bookcraft.infra.logging import configure_logging
from bookcraft.infra.observability import configure_tracing
from bookcraft.infra.readiness import ReadinessChecker
from bookcraft.infra.schemas import HealthResponse, ReadinessResponse

REQUESTS_TOTAL = Counter(
    "chatbot_http_requests_total",
    "Total HTTP requests handled by the BookCraft API.",
    ["path"],
)
READINESS_LATENCY = Histogram(
    "chatbot_readiness_latency_seconds",
    "Latency for readiness checks.",
)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    configure_sentry(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_tracing(app, resolved_settings)
        structlog.get_logger(__name__).info(
            "app_started",
            app_name=resolved_settings.app_name,
            app_env=resolved_settings.app_env,
        )
        yield
        structlog.get_logger(__name__).info("app_stopped")

    app = FastAPI(
        title="BookCraft AI Chatbot",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.readiness_checker = ReadinessChecker(resolved_settings)

    @app.get("/healthz", response_model=HealthResponse, tags=["system"])
    async def healthz() -> HealthResponse:
        REQUESTS_TOTAL.labels(path="/healthz").inc()
        return HealthResponse(
            status="ok",
            app=resolved_settings.app_name,
            environment=resolved_settings.app_env,
        )

    @app.get("/readyz", response_model=ReadinessResponse, tags=["system"])
    async def readyz(response: Response) -> ReadinessResponse:
        REQUESTS_TOTAL.labels(path="/readyz").inc()
        with READINESS_LATENCY.time():
            readiness_checker = cast(ReadinessChecker, app.state.readiness_checker)
            result = await readiness_checker.check()
        if result.status != "ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return result

    @app.get("/metrics", tags=["system"])
    async def metrics() -> Response:
        REQUESTS_TOTAL.labels(path="/metrics").inc()
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def configure_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.05,
    )


app = create_app()
