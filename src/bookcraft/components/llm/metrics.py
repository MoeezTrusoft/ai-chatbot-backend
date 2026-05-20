from prometheus_client import Counter, Histogram

LLM_CALLS = Counter("llm_calls_total", "LLM calls by purpose/provider.", ["provider", "purpose"])
LLM_LATENCY = Histogram(
    "llm_call_latency_seconds",
    "LLM call latency by purpose/provider.",
    ["provider", "purpose"],
)
