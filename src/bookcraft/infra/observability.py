from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram

from bookcraft.infra.config import Settings

STAGE_LATENCY = Histogram(
    "chat_stage_latency_seconds",
    "Per-stage latency in ChatService.handle_turn.",
    ["stage"],
)

CONTEXT_HINT_DROPPED = Counter(
    "context_hint_dropped_total",
    "Context-pack response_hint sources dropped by token budgeting, by source label.",
    ["source"],
)


def configure_tracing(app: FastAPI, settings: Settings) -> None:
    current_provider = trace.get_tracer_provider()
    if not isinstance(current_provider, TracerProvider):
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: settings.app_name}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
        trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
