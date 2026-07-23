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
    # Response generation runs on Opus for the highest-quality customer-facing reply.
    # Kept SEPARATE from extraction: the LLM metadata extractor + CSR summarizer are
    # high-frequency structured-JSON tasks where Opus adds cost/latency with no accuracy
    # benefit, so they stay on a cheaper model via `anthropic_extraction_model`.
    anthropic_response_model: str = "claude-opus-4-8"
    anthropic_extraction_model: str = "claude-sonnet-5"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_intent_model: str = "gpt-5.4-mini"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "http://deepseek-internal:8000/v1"
    deepseek_intent_model: str = "deepseek-chat"
    deepseek_intent_enabled: bool = False
    llm_provider_mode: Literal["mock", "live"] = "mock"
    llm_request_timeout_seconds: float = 60.0  # raised: let LLM take its time; no timeout fallback
    llm_bounded_timeouts_enabled: bool = False
    llm_read_timeout_generation_seconds: float = 20.0
    llm_read_timeout_extraction_seconds: float = 8.0
    prompt_cache_enabled: bool = False
    event_log_batching_enabled: bool = False
    trg_background_persist_enabled: bool = False
    llm_extraction_overlap_enabled: bool = False
    trimatch_event_evidence_summary: bool = False
    trg_event_rebuild_enabled: bool = False
    project_fact_partitioning_enabled: bool = False
    trimatch_compiled_index_enabled: bool = False
    trimatch_semantic_embeddings_enabled: bool = False
    response_streaming_enabled: bool = False
    contradiction_confirmation_enabled: bool = False
    extraction_value_types_enabled: bool = False
    # Wave 2: completion of the partially-implemented plan tasks.
    trg_question_matching_enabled: bool = False  # P2-T1: slot/embedding answer matching
    trg_answer_match_threshold: float = 0.6  # P2-T1: cosine threshold for answer→question
    trg_repetition_edges_v2: bool = False  # P2-T7: REPEATS edge to prior occurrence
    context_pack_budget_enabled: bool = False  # P4-T3: hint-source token budgeting
    context_pack_hint_token_budget: int = 1200  # P4-T3: max approx tokens of response_hint
    # Bound structured-state growth (advisory item 5): cap how many known-facts are
    # RENDERED into the response prompt per turn so a long, fact-rich thread does not
    # creep the prompt upward every turn. Contact + active-service facts are always kept;
    # the rest are selected by confidence. 0 = unbounded (render all). Persisted state is
    # never pruned — only what is rendered per turn is bounded.
    context_pack_render_fact_cap: int = 12
    # RAG hygiene (advisory item 4): collapse identical / near-duplicate RAG chunk texts
    # within a single prompt so the same passage is not injected multiple times in one
    # turn (saves tokens and shrinks verbatim-bleed surface). Retrieval is unchanged.
    rag_within_prompt_dedup_enabled: bool = True
    staged_pipeline_enabled: bool = False  # P4-T2: staged TurnContext pipeline (foundation)
    # Always ask the customer for a phone number once before booking, even when an
    # email is already captured (the customer can still decline and proceed email-only).
    consultation_require_phone: bool = True

    # CSR Node.js backend — direct API call after consultation is booked so the
    # appointment always appears on the CSR dashboard regardless of action-event sync.
    # Both services run on the same server → use localhost + CSR Node port (5050).
    csr_node_api_url: str = "http://localhost:5050"
    csr_node_consultation_timeout_seconds: float = 10.0

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
    # Output-token ceiling for response generation. Billed on tokens actually
    # produced, so headroom is free; 1024 truncated multi-question replies, and at
    # 2048 Opus occasionally hit stop_reason=max_tokens (truncated → validation
    # reject → template fallback), so raised to 3072.
    response_max_tokens: int = 3072
    # "disabled" | "adaptive" | "omit" — sent as the request's `thinking` field.
    # MUST stay explicit: claude-sonnet-4-6 read an omitted field as no-thinking,
    # claude-sonnet-5 reads the same omission as adaptive, and thinking shares the
    # max_tokens budget with the reply (chat 5876). Do not use budget_tokens —
    # claude-sonnet-5 returns 400 for it; use effort under "adaptive" instead.
    response_thinking_mode: str = "disabled"
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
