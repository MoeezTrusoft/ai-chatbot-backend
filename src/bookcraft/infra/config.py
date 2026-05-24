from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    app_name: str = "bookcraft-chatbot"
    app_host: str = "0.0.0.0"  # noqa: S104 - local ASGI default, configurable by env.
    app_port: int = 8000
    readiness_check_externals: bool = False
    language_guard_enabled: bool = True
    response_repair_enabled: bool = False  # TEMP: LLM repair disabled per product decision
    database_url: str = "postgresql+asyncpg://bookcraft:bookcraft_dev@localhost:55432/bookcraft"
    database_replica_url: str | None = None
    database_pool_size: int = 20
    database_max_overflow: int = 10

    redis_url: str = "redis://localhost:6379/0"
    redis_hot_ttl_hours: int = 24
    redis_idempotency_ttl_hours: int = 24
    redis_relation_cache_ttl_hours: int = 24

    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_user: str | None = None
    elasticsearch_password: str | None = None
    elasticsearch_index_prefix: str = "bookcraft_"

    tei_url: str = "http://localhost:8080"
    tei_timeout_seconds: float = 10.0
    tei_batch_size: int = 128
    tei_degraded_mode_enabled: bool = True
    embedding_dimensions: int = 384

    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_haiku_model: str = "claude-haiku-4-5-20251001"
    anthropic_sonnet_model: str = "claude-sonnet-4-6"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_intent_model: str = "gpt-5.4-mini"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "http://deepseek-internal:8000/v1"
    deepseek_intent_model: str = "deepseek-chat"
    deepseek_intent_enabled: bool = False
    llm_provider_mode: Literal["mock", "live"] = "mock"
    llm_request_timeout_seconds: float = 60.0  # raised: let LLM take its time; no timeout fallback

    nda_mode: Literal["manual", "verifier_gated", "autonomous"] = "manual"
    agreement_mode: Literal["manual", "verifier_gated", "autonomous"] = "manual"
    nda_template_version: str = "v1.0"
    agreement_template_version: str = "v1.0"
    document_template_dir: str = "data/templates"
    document_output_dir: str = "data/generated/documents"
    document_pdf_rendering_enabled: bool = False
    document_retraction_hours: int = 24
    s3_documents_bucket: str = "bookcraft-documents"
    s3_region: str = "us-east-1"
    document_signed_url_ttl_hours: int = 24

    email_provider: Literal["sendgrid", "ses"] = "sendgrid"
    sendgrid_api_key: str | None = None
    email_from_address: str = "hello@bookcraft.ai"
    email_from_name: str = "BookCraft AI"
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "BookCraft Publishers"
    smtp_use_tls: bool = True

    trimatch_mode: Literal["shadow", "vote_only", "shortcut_enabled"] = "shadow"
    trimatch_shortcut_layers: str = ""
    trimatch_shortcut_threshold: float = 0.95
    trimatch_autocorrect_enabled: bool = False
    trimatch_autoapprove_enabled: bool = False
    trimatch_rule_dir: str = "data/trimatch/rules"
    trimatch_eval_dir: str = "data/trimatch/eval"
    trimatch_funnel_stage_weight: float = 0.5
    trimatch_fuzzy_enabled: bool = False
    trimatch_extra_mode: Literal[
        "off",
        "shadow",
        "advisory",
        "tiebreaker_candidate",
        "shortcut_candidate",
    ] = "off"
    trimatch_extra_rule_dir: str = "data/trimatch/reinforcement/staged_from_reviews"
    trimatch_extra_fuzzy_enabled: bool = False

    funnel_signal_mode: Literal["shadow", "vote_only"] = "shadow"
    funnel_rule_source_path: str = "data/funnel/funnel_stage_intents.sample.json"
    funnel_rule_build_dir: str = "data/funnel/build"
    preprocessor_sidecar_dir: str = "data/trimatch/sidecars"

    rag_top_k: int = 8
    rag_max_tokens_per_chunk: int = 200
    rag_chunk_overlap_tokens: int = 50
    rag_index_alias: str = "bookcraft_rag_current"
    rag_index_version: str = "bookcraft_rag_v1"
    rag_source_dir: str = "data/rag-corpus/source_markdown"
    rag_build_dir: str = "data/rag-corpus/build"
    rag_strict_verifier: bool = True
    pricing_rule_dir: str = "data/pricing"
    pricing_strict_verifier: bool = True
    pricing_allow_placeholder_rules: bool = False
    pricing_quote_valid_days: int = 14
    pricing_engine_version: Literal["v1", "v2"] = "v2"
    pricing_v2_config_dir: str = "data/pricing/v2"
    pricing_v2_values_approved: bool = False
    portfolio_samples_registry_path: str = "data/portfolio/samples.registry.js"
    portfolio_genre_hierarchy_path: str = "data/portfolio/genre_hierarchy_links.json"
    portfolio_samples_docx_path: str = "data/portfolio/portfolio_samples.docx"
    sonnet_max_tokens: int = 600
    haiku_max_tokens: int = 2048
    intent_ensemble_timeout_seconds: float = 30.0  # raised: don't time out intent classification
    deepseek_timeout_seconds: float = 4.0
    shared_processor_cache_size: int = 1000
    trg_hot_nodes_limit: int = 24
    trg_compact_keep: int = 12
    trg_relation_fast_path_threshold: float = 0.85
    trg_compliance_threshold_default: float = 0.62

    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    sentry_dsn: str | None = None
    sentry_environment: str = "dev"
    metrics_public: bool = False
    metrics_bearer_token: str | None = None
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    api_auth_mode: Literal["off", "jwt"] = "off"
    jwt_signing_key: str | None = None
    jwt_ttl_hours: int = 24
    ws_allowed_origins: str = "http://localhost:3000,http://localhost:8000"
    rate_limit_per_ip_per_minute: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
