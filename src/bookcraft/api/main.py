from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from uuid import uuid4

import sentry_sdk
import structlog
from fastapi import FastAPI, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sentry_sdk.integrations.fastapi import FastApiIntegration

from bookcraft.api.chat import router as chat_router
from bookcraft.components.extraction import CombinedExtractor, StateApplier
from bookcraft.components.intent import build_mock_ensemble_classifier
from bookcraft.components.language_guard import LanguageGuard
from bookcraft.components.portfolio import PortfolioEngine, PortfolioRegistry
from bookcraft.components.preprocessor import EmbeddingClient, SharedPreprocessor, load_sidecars
from bookcraft.components.pricing import PricingTimelineEngine
from bookcraft.components.response import ResponseFormatter, SonnetResponseGenerator
from bookcraft.components.storage.db import create_engine, create_session_factory
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.components.trimatch import (
    RuleRepository,
    TriMatchEngine,
    TriMatchLayer,
    TriMatchMode,
)
from bookcraft.infra.cache import CacheClient, CacheKeyBuilder, create_redis_client
from bookcraft.infra.config import Settings, get_settings
from bookcraft.infra.logging import configure_logging
from bookcraft.infra.observability import configure_tracing
from bookcraft.infra.readiness import ReadinessChecker
from bookcraft.infra.schemas import HealthResponse, ReadinessResponse
from bookcraft.services.chat import ChatService

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
        await app.state.db_engine.dispose()
        structlog.get_logger(__name__).info("app_stopped")

    app = FastAPI(
        title="BookCraft AI Chatbot",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.readiness_checker = ReadinessChecker(resolved_settings)
    db_engine = create_engine(resolved_settings)
    session_factory = create_session_factory(db_engine)
    thread_repository = ThreadRepository(session_factory=session_factory)
    app.state.db_engine = db_engine
    app.state.chat_service = build_chat_service(
        resolved_settings,
        thread_repository=thread_repository,
    )
    app.include_router(chat_router)

    @app.middleware("http")
    async def bind_trace_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        response = await call_next(request)
        response.headers["x-correlation-id"] = correlation_id
        return response

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


def build_chat_service(
    settings: Settings,
    *,
    thread_repository: ThreadRepository | None = None,
) -> ChatService:
    sidecars = load_sidecars(settings.preprocessor_sidecar_dir)
    cache_client = None
    if settings.readiness_check_externals:
        cache_client = cast(CacheClient, create_redis_client(settings))
    key_builder = CacheKeyBuilder(environment=settings.app_env)
    embedding_client = EmbeddingClient(
        tei_url=settings.tei_url,
        timeout_seconds=settings.tei_timeout_seconds,
        dimensions=settings.embedding_dimensions,
        degraded_mode_enabled=settings.tei_degraded_mode_enabled,
        cache=cache_client,
        keys=key_builder,
    )
    return ChatService(
        language_guard=LanguageGuard(enabled=settings.language_guard_enabled),
        preprocessor=SharedPreprocessor(sidecars=sidecars, embedding_client=embedding_client),
        intent_classifier=build_mock_ensemble_classifier(
            timeout_seconds=settings.intent_ensemble_timeout_seconds,
            trimatch_funnel_stage_weight=settings.trimatch_funnel_stage_weight,
        ),
        extractor=CombinedExtractor(),
        state_applier=StateApplier(),
        response_generator=SonnetResponseGenerator(),
        formatter=ResponseFormatter(),
        pricing_engine=PricingTimelineEngine.from_config_dir(
            Path(settings.pricing_v2_config_dir),
            values_approved=settings.pricing_v2_values_approved,
        ),
        portfolio_engine=PortfolioEngine(
            PortfolioRegistry.from_files(
                samples_registry_path=settings.portfolio_samples_registry_path,
                genre_hierarchy_path=settings.portfolio_genre_hierarchy_path,
                portfolio_docx_path=settings.portfolio_samples_docx_path,
            )
        ),
        trimatch_engine=build_trimatch_engine(settings),
        thread_repository=thread_repository,
    )


def build_trimatch_engine(settings: Settings) -> TriMatchEngine:
    shortcut_layers = {
        TriMatchLayer(layer.strip())
        for layer in settings.trimatch_shortcut_layers.split(",")
        if layer.strip()
    }
    return TriMatchEngine(
        rule_pack=RuleRepository(settings.trimatch_rule_dir).load_active_rules(),
        mode=TriMatchMode(settings.trimatch_mode),
        shortcut_layers=shortcut_layers,
        shortcut_threshold=settings.trimatch_shortcut_threshold,
        funnel_stage_weight=settings.trimatch_funnel_stage_weight,
        fuzzy_enabled=settings.trimatch_fuzzy_enabled,
    )


app = create_app()
