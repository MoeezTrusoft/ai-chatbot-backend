from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import sentry_sdk
import structlog
from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bookcraft.api.admin_analysis import router as admin_analysis_router
from bookcraft.api.chat import router as chat_router
from bookcraft.api.correlation import sanitize_correlation_id
from bookcraft.api.errors import ErrorResponse
from bookcraft.api.metrics_auth import is_metrics_request_allowed
from bookcraft.api.security import parse_allowed_origins
from bookcraft.components.actions import SalesActionDispatcher
from bookcraft.components.analysis import LiveTraceStore
from bookcraft.components.consultations import (
    ConsultationActionService,
    ConsultationRepository,
)
from bookcraft.components.document_actions import (
    AgreementActionService,
    DocumentRequestRepository,
    NDAActionService,
)
from bookcraft.components.documents.engine import DocumentEngine
from bookcraft.components.documents.registry import DocumentTemplateRegistry
from bookcraft.components.documents.tools import register_document_tools
from bookcraft.components.extraction import CombinedExtractor, StateApplier
from bookcraft.components.context import ContextPackBuilder
from bookcraft.components.context.entity_index import ConversationEntityIndex
from bookcraft.components.csr.summarizer import CsrContextSummarizer
from bookcraft.components.extraction.llm_extractor import LLMMetadataExtractor
from bookcraft.components.trg.checkpointer import ConversationCheckpointer
from bookcraft.components.trg.fact_store import TRGFactStore
from bookcraft.components.intent import (
    EnsembleIntentClassifier,
    LLMIntentProvider,
    build_live_ensemble_classifier,
    build_mock_ensemble_classifier,
)
from bookcraft.components.language_guard import LanguageGuard
from bookcraft.components.leads import LeadRepository, LeadService
import bookcraft.components.llm.adapters as _adapters_module
from bookcraft.components.llm import AnthropicAdapter, DeepSeekAdapter, OpenAIAdapter
from bookcraft.components.portfolio import PortfolioEngine, PortfolioRegistry
from bookcraft.components.portfolio.tools import register_portfolio_tools
from bookcraft.components.portfolio_actions import (
    PortfolioActionService,
    PortfolioViewRepository,
)
from bookcraft.components.preprocessor import EmbeddingClient, SharedPreprocessor, load_sidecars
from bookcraft.components.pricing import PricingTimelineEngine
from bookcraft.components.pricing.tools import register_pricing_tools
from bookcraft.components.pricing_actions import (
    PricingActionService,
    PricingQuoteRepository,
)
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.components.response import ResponseFormatter, SonnetResponseGenerator
from bookcraft.components.storage.db import create_engine, create_session_factory
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.components.trg import (
    InMemoryGraphRepository,
    RedisHotGraphStore,
    TemporalRelationGraphEngine,
)
from bookcraft.components.trimatch import (
    RuleRepository,
    TriMatchEngine,
    TriMatchLayer,
    TriMatchMode,
)
from bookcraft.infra.cache import CacheClient, CacheKeyBuilder, create_redis_client
from bookcraft.infra.config import Settings, get_settings
from bookcraft.infra.email import SMTPEmailClient
from bookcraft.infra.logging import configure_logging
from bookcraft.infra.observability import configure_tracing
from bookcraft.infra.rate_limit import InMemoryRateLimiter, RedisRateLimiter, RedisRateLimitStore
from bookcraft.infra.readiness import ReadinessChecker
from bookcraft.infra.redaction import redact_text
from bookcraft.infra.schemas import HealthResponse, ReadinessResponse
from bookcraft.services.chat import ChatService
from bookcraft.tools import (
    DbToolAuditSink,
    IdempotencyStore,
    MemoryAuditSink,
    MemoryCache,
    ToolDispatcher,
    ToolGatingPolicy,
    ToolRegistry,
)

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
        chat_service = getattr(app.state, "chat_service", None)
        gen = getattr(chat_service, "response_generator", None)
        adapter = getattr(gen, "adapter", None)
        structlog.get_logger(__name__).info(
            "app_started",
            app_name=resolved_settings.app_name,
            app_env=resolved_settings.app_env,
            response_generator_adapter="anthropic" if adapter is not None else "none",
            response_generator_provider=getattr(gen, "provider_name", "unknown"),
            response_generator_contract_mode=(
                "production_strict"
                if resolved_settings.app_env not in {"test", "dev", "development", "local"}
                else "test_dev"
            ),
        )
        # Warm up the shared HTTP connection pool so the first LLM call does
        # not pay for client initialisation.  Skipped in test mode because
        # adapters use MockLLMAdapter there and we never want a real client.
        if resolved_settings.app_env != "test" and resolved_settings.llm_provider_mode != "mock":
            await _adapters_module.get_shared_client(
                read_timeout=(
                    resolved_settings.llm_read_timeout_generation_seconds
                    if resolved_settings.llm_bounded_timeouts_enabled
                    else None
                )
            )
        yield
        await _adapters_module.close_shared_client()
        db_engine = getattr(app.state, "db_engine", None)
        if db_engine is not None:
            await db_engine.dispose()
        rate_limit_client = getattr(app.state, "rate_limit_client", None)
        if rate_limit_client is not None:
            await rate_limit_client.aclose()
        elasticsearch_client = getattr(app.state, "elasticsearch_client", None)
        if elasticsearch_client is not None:
            await elasticsearch_client.close()
        structlog.get_logger(__name__).info("app_stopped")

    app = FastAPI(
        title="BookCraft AI Chatbot",
        version="0.1.0",
        lifespan=lifespan,
    )

    allowed_origins = sorted(parse_allowed_origins(resolved_settings))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )
    app.state.settings = resolved_settings
    app.state.readiness_checker = ReadinessChecker(resolved_settings)
    rate_limit_client = None
    if resolved_settings.app_env != "test":
        rate_limit_client = create_redis_client(resolved_settings)

    app.state.rate_limit_client = rate_limit_client
    app.state.rate_limiter = (
        InMemoryRateLimiter(limit_per_minute=resolved_settings.rate_limit_per_ip_per_minute)
        if rate_limit_client is None
        else RedisRateLimiter(
            store=RedisRateLimitStore(rate_limit_client),
            keys=CacheKeyBuilder(environment=resolved_settings.app_env),
            limit_per_minute=resolved_settings.rate_limit_per_ip_per_minute,
        )
    )
    thread_repository = None
    session_factory = None
    rag_retriever = None
    trg_engine = build_trg_engine(
        resolved_settings,
        cache_client=cast(CacheClient, rate_limit_client)
        if rate_limit_client is not None
        else None,
    )
    if resolved_settings.app_env != "test":
        db_engine = create_engine(resolved_settings)
        session_factory = create_session_factory(db_engine)
        thread_repository = ThreadRepository(session_factory=session_factory)
        app.state.db_engine = db_engine

        elasticsearch_client = AsyncElasticsearch(
            hosts=[resolved_settings.elasticsearch_url],
            basic_auth=(
                resolved_settings.elasticsearch_user,
                resolved_settings.elasticsearch_password,
            )
            if resolved_settings.elasticsearch_user and resolved_settings.elasticsearch_password
            else None,
            request_timeout=resolved_settings.tei_timeout_seconds,
        )
        app.state.elasticsearch_client = elasticsearch_client
        rag_retriever = RagRetriever(
            client=elasticsearch_client,
            index_alias=resolved_settings.rag_index_alias,
        )

    app.state.chat_service = build_chat_service(
        resolved_settings,
        thread_repository=thread_repository,
        session_factory=session_factory,
        rag_retriever=rag_retriever,
        trg_engine=trg_engine,
        elasticsearch_client=elasticsearch_client if resolved_settings.app_env != "test" else None,
    )
    app.include_router(chat_router)
    app.include_router(admin_analysis_router)

    @app.middleware("http")
    async def bind_trace_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = sanitize_correlation_id(request.headers.get("x-correlation-id"))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        try:
            response = await call_next(request)
        except Exception as exc:
            structlog.get_logger(__name__).exception(
                "unhandled_http_exception",
                correlation_id=correlation_id,
                method=request.method,
                path=request.url.path,
                exception_class=exc.__class__.__name__,
                error=redact_text(str(exc)),
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=ErrorResponse(
                    error="internal_error",
                    correlation_id=correlation_id,
                ).model_dump(mode="json"),
                headers={"x-correlation-id": correlation_id},
            )

        response.headers["x-correlation-id"] = correlation_id
        return response

    if resolved_settings.app_env == "test":

        @app.get("/_test/crash", include_in_schema=False)
        async def test_crash() -> None:
            raise RuntimeError("boom author@example.com +92 300 1234567")

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
    async def metrics(request: Request) -> Response:
        REQUESTS_TOTAL.labels(path="/metrics").inc()
        if not is_metrics_request_allowed(request, resolved_settings):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "metrics_forbidden"},
            )
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
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    rag_retriever: RagRetriever | None = None,
    trg_engine: TemporalRelationGraphEngine | None = None,
    elasticsearch_client: AsyncElasticsearch | None = None,
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
    pricing_engine = PricingTimelineEngine.from_config_dir(
        Path(settings.pricing_v2_config_dir),
        values_approved=settings.pricing_v2_values_approved,
    )
    portfolio_engine = PortfolioEngine(
        PortfolioRegistry.from_files(
            samples_registry_path=settings.portfolio_samples_registry_path,
            genre_hierarchy_path=settings.portfolio_genre_hierarchy_path,
            portfolio_docx_path=settings.portfolio_samples_docx_path,
        )
    )
    lead_service = (
        None
        if session_factory is None
        else LeadService(repository=LeadRepository(session_factory=session_factory))
    )
    pricing_action_service = (
        None
        if session_factory is None
        else PricingActionService(
            pricing_engine=pricing_engine,
            repository=PricingQuoteRepository(session_factory=session_factory),
        )
    )
    document_engine = DocumentEngine(
        registry=DocumentTemplateRegistry(settings.document_template_dir),
        output_dir=settings.document_output_dir,
        pdf_rendering_enabled=settings.document_pdf_rendering_enabled,
    )
    email_client = SMTPEmailClient(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        from_email=settings.smtp_from_email or settings.email_from_address,
        from_name=settings.smtp_from_name or settings.email_from_name,
        use_tls=settings.smtp_use_tls,
        enabled=settings.smtp_enabled,
    )
    portfolio_action_service = (
        None
        if session_factory is None
        else PortfolioActionService(
            portfolio_engine=portfolio_engine,
            repository=PortfolioViewRepository(session_factory=session_factory),
        )
    )
    document_request_repository = (
        None
        if session_factory is None
        else DocumentRequestRepository(session_factory=session_factory)
    )
    nda_action_service = (
        None
        if document_request_repository is None
        else NDAActionService(
            document_engine=document_engine,
            repository=document_request_repository,
            email_client=email_client,
        )
    )
    agreement_action_service = (
        None
        if document_request_repository is None
        else AgreementActionService(
            document_engine=document_engine,
            repository=document_request_repository,
            email_client=email_client,
        )
    )
    consultation_action_service = (
        None
        if session_factory is None
        else ConsultationActionService(
            repository=ConsultationRepository(session_factory=session_factory),
            csr_node_api_url=settings.csr_node_api_url or None,
            csr_node_timeout=settings.csr_node_consultation_timeout_seconds,
        )
    )
    response_generator = build_response_generator(settings)
    _rg_adapter = getattr(response_generator, "adapter", None)
    llm_metadata_extractor = (
        LLMMetadataExtractor(adapter=_rg_adapter) if _rg_adapter is not None else None
    )
    trg_fact_store = (
        TRGFactStore(session_factory=session_factory) if session_factory is not None else None
    )
    conversation_checkpointer = (
        ConversationCheckpointer(session_factory=session_factory)
        if session_factory is not None
        else None
    )
    entity_index = (
        ConversationEntityIndex(
            client=elasticsearch_client,
            embedding_client=embedding_client,
        )
        if elasticsearch_client is not None
        else None
    )
    csr_summarizer = CsrContextSummarizer(adapter=_rg_adapter)
    return ChatService(
        language_guard=LanguageGuard(enabled=settings.language_guard_enabled),
        preprocessor=SharedPreprocessor(sidecars=sidecars, embedding_client=embedding_client),
        intent_classifier=build_intent_classifier(settings),
        extractor=CombinedExtractor(),
        state_applier=StateApplier(),
        consultation_require_phone=settings.consultation_require_phone,
        response_generator=response_generator,
        llm_metadata_extractor=llm_metadata_extractor,
        trg_fact_store=trg_fact_store,
        conversation_checkpointer=conversation_checkpointer,
        entity_index=entity_index,
        csr_summarizer=csr_summarizer,
        formatter=ResponseFormatter(),
        context_pack_builder=ContextPackBuilder(
            budget_enabled=settings.context_pack_budget_enabled,
            hint_token_budget=settings.context_pack_hint_token_budget,
        ),
        rag_retriever=rag_retriever,
        pricing_engine=pricing_engine,
        portfolio_engine=portfolio_engine,
        tool_dispatcher=build_tool_dispatcher(
            settings,
            pricing_engine,
            portfolio_engine,
            session_factory=session_factory,
        ),
        environment=settings.app_env,
        response_repair_enabled=settings.response_repair_enabled,
        trg_engine=trg_engine,
        trimatch_engine=build_trimatch_engine(settings),
        trimatch_shadow_engine=build_trimatch_shadow_engine(settings),
        trimatch_extra_mode=settings.trimatch_extra_mode,
        action_dispatcher=SalesActionDispatcher(
            lead_service=lead_service,
            consultation_action_service=consultation_action_service,
            pricing_action_service=pricing_action_service,
            portfolio_action_service=portfolio_action_service,
            nda_action_service=nda_action_service,
            agreement_action_service=agreement_action_service,
            app_env=settings.app_env,
        ),
        trace_store=LiveTraceStore(Path("reports/live_traces/chat_turns.jsonl")),
        thread_repository=thread_repository,
    )


def build_tool_dispatcher(
    settings: Settings,
    pricing_engine: PricingTimelineEngine,
    portfolio_engine: PortfolioEngine,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ToolDispatcher:
    registry = ToolRegistry()
    register_pricing_tools(registry, pricing_engine)
    register_portfolio_tools(registry, portfolio_engine)
    register_document_tools(
        registry,
        DocumentEngine(
            registry=DocumentTemplateRegistry(settings.document_template_dir),
            output_dir=settings.document_output_dir,
            pdf_rendering_enabled=settings.document_pdf_rendering_enabled,
        ),
    )
    key_builder = CacheKeyBuilder(environment=settings.app_env)
    audit_sink = (
        MemoryAuditSink()
        if session_factory is None or settings.app_env == "test"
        else DbToolAuditSink(session_factory=session_factory)
    )
    return ToolDispatcher(
        registry=registry,
        idempotency_store=IdempotencyStore(
            client=MemoryCache(),
            keys=key_builder,
            ttl_seconds=settings.redis_idempotency_ttl_hours * 3600,
        ),
        audit_sink=audit_sink,
        gating_policy=ToolGatingPolicy(
            nda_mode=settings.nda_mode,
            agreement_mode=settings.agreement_mode,
        ),
    )


def build_trg_engine(
    settings: Settings,
    *,
    cache_client: CacheClient | None = None,
) -> TemporalRelationGraphEngine:
    repository = (
        InMemoryGraphRepository()
        if cache_client is None
        else RedisHotGraphStore(
            client=cache_client,
            keys=CacheKeyBuilder(environment=settings.app_env),
            ttl_seconds=settings.redis_hot_ttl_hours * 3600,
        )
    )
    return TemporalRelationGraphEngine(
        repository=repository,
        compact_keep=settings.trg_compact_keep,
        question_matching_enabled=settings.trg_question_matching_enabled,
        answer_match_threshold=settings.trg_answer_match_threshold,
        repetition_edges_v2=settings.trg_repetition_edges_v2,
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


def build_trimatch_shadow_engine(settings: Settings) -> TriMatchEngine | None:
    if settings.trimatch_extra_mode == "off":
        return None

    if settings.trimatch_extra_mode == "shortcut_candidate":
        shortcut_layers = {
            TriMatchLayer(layer.strip())
            for layer in settings.trimatch_shortcut_layers.split(",")
            if layer.strip()
        }
        return TriMatchEngine(
            rule_pack=RuleRepository(settings.trimatch_extra_rule_dir).load_active_rules(),
            mode=TriMatchMode.SHORTCUT_ENABLED,
            shortcut_layers=shortcut_layers,
            shortcut_threshold=settings.trimatch_shortcut_threshold,
            funnel_stage_weight=0.0,
            fuzzy_enabled=settings.trimatch_extra_fuzzy_enabled,
        )

    return TriMatchEngine(
        rule_pack=RuleRepository(settings.trimatch_extra_rule_dir).load_active_rules(),
        mode=TriMatchMode.SHADOW,
        shortcut_layers=set(),
        shortcut_threshold=1.0,
        funnel_stage_weight=0.0,
        fuzzy_enabled=settings.trimatch_extra_fuzzy_enabled,
    )


def build_intent_classifier(settings: Settings) -> EnsembleIntentClassifier:
    if settings.app_env == "test" or settings.llm_provider_mode == "mock":
        return build_mock_ensemble_classifier(
            timeout_seconds=settings.intent_ensemble_timeout_seconds,
            trimatch_funnel_stage_weight=settings.trimatch_funnel_stage_weight,
        )

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required when LLM_PROVIDER_MODE=live")
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER_MODE=live")

    _extraction_read_timeout = (
        settings.llm_read_timeout_extraction_seconds
        if settings.llm_bounded_timeouts_enabled
        else None
    )
    providers = [
        LLMIntentProvider(
            name="claude_haiku",
            adapter=AnthropicAdapter(
                api_key=settings.anthropic_api_key,
                base_url=settings.anthropic_base_url,
                timeout_seconds=settings.llm_request_timeout_seconds,
                model=settings.anthropic_haiku_model,
                name="claude_haiku",
                read_timeout=_extraction_read_timeout,
                prompt_cache_enabled=settings.prompt_cache_enabled,
            ),
        ),
        LLMIntentProvider(
            name="openai_gpt_5_4_mini",
            adapter=OpenAIAdapter(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.llm_request_timeout_seconds,
                model=settings.openai_intent_model,
                name="openai_gpt_5_4_mini",
                read_timeout=_extraction_read_timeout,
            ),
        ),
    ]

    if settings.deepseek_intent_enabled:
        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required when DEEPSEEK_INTENT_ENABLED=true")
        providers.append(
            LLMIntentProvider(
                name="deepseek_v3",
                adapter=DeepSeekAdapter(
                    api_key=settings.deepseek_api_key,
                    base_url=settings.deepseek_base_url,
                    timeout_seconds=settings.deepseek_timeout_seconds,
                    model=settings.deepseek_intent_model,
                ),
            )
        )

    return build_live_ensemble_classifier(
        providers=providers,
        timeout_seconds=settings.intent_ensemble_timeout_seconds,
        trimatch_funnel_stage_weight=settings.trimatch_funnel_stage_weight,
    )


def build_response_generator(settings: Settings) -> SonnetResponseGenerator:
    from bookcraft.components.response.contracts import (
        is_production_like,
    )

    logger = structlog.get_logger(__name__)
    production_like = is_production_like(settings.app_env)

    # Test env: always use the no-adapter mock to preserve unit-test isolation.
    if settings.app_env == "test":
        logger.info(
            "response_generator_adapter",
            response_generator_adapter="none",
            response_generator_provider="template_no_adapter",
            response_generator_contract_mode="test_dev",
        )
        return SonnetResponseGenerator()

    # If ANTHROPIC_API_KEY is present, wire the real Anthropic adapter regardless
    # of llm_provider_mode — key presence is the authoritative signal.
    if settings.anthropic_api_key:
        _generation_read_timeout = (
            settings.llm_read_timeout_generation_seconds
            if settings.llm_bounded_timeouts_enabled
            else None
        )
        adapter = AnthropicAdapter(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            timeout_seconds=settings.llm_request_timeout_seconds,
            model=settings.anthropic_sonnet_model,
            name="claude_sonnet",
            read_timeout=_generation_read_timeout,
            prompt_cache_enabled=settings.prompt_cache_enabled,
        )
        contract_mode = "production_strict" if production_like else "test_dev"
        logger.info(
            "response_generator_adapter",
            response_generator_adapter="anthropic",
            response_generator_provider="claude_sonnet",
            response_generator_contract_mode=contract_mode,
            anthropic_model=settings.anthropic_sonnet_model,
        )
        return SonnetResponseGenerator(
            provider_name="claude_sonnet",
            adapter=adapter,
        )

    # No API key in production-like environment or when live mode is explicit.
    if production_like or settings.llm_provider_mode == "live":
        raise RuntimeError(
            f"ANTHROPIC_API_KEY is required in production-like environment "
            f"(app_env={settings.app_env!r}, llm_provider_mode={settings.llm_provider_mode!r}). "
            "Set ANTHROPIC_API_KEY or switch LLM_PROVIDER_MODE=mock for local dev."
        )

    # Dev/local without API key — use mock and warn.
    logger.warning(
        "response_generator_adapter",
        response_generator_adapter="none",
        response_generator_provider="template_no_adapter",
        response_generator_contract_mode="test_dev",
        warning="no ANTHROPIC_API_KEY in non-test env; responses will use template fallback",
    )
    return SonnetResponseGenerator()


app = create_app()
