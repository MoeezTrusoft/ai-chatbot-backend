import json
import math

from prometheus_client import Counter, Histogram

LLM_CALLS = Counter("llm_calls_total", "LLM calls by purpose/provider.", ["provider", "purpose"])
LLM_LATENCY = Histogram(
    "llm_call_latency_seconds",
    "LLM call latency by purpose/provider.",
    ["provider", "purpose"],
)

PROVIDER_TOKENS = Counter(
    "llm_provider_tokens_total",
    "Actual usage reported by the provider.",
    ["provider", "model", "purpose", "token_type"],
)
PROVIDER_COST = Counter(
    "llm_provider_cost_usd_total",
    "Cost computed from actual usage and configured rates.",
    ["provider", "model", "purpose"],
)
_prices: dict[str, dict[str, float]] = {}


def configure_prices(prices: dict[str, dict[str, float]]) -> None:
    global _prices
    _prices = prices


def record_usage(raw: str, *, provider: str, model: str, purpose: str) -> None:
    """Never log prompts, completions or credentials; absent usage is not zero usage."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict) or not isinstance(data.get("usage"), dict):
        return
    usage = data["usage"]
    token_types = {
        "input": usage.get("input_tokens", usage.get("prompt_tokens")),
        "output": usage.get("output_tokens", usage.get("completion_tokens")),
        "cache_read": usage.get("cache_read_input_tokens"),
        "cache_write": usage.get("cache_creation_input_tokens"),
    }
    # OpenAI prompt_tokens includes cached tokens; bill those separately.
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("cached_tokens"), int):
        token_types["cache_read"] = details["cached_tokens"]
        if isinstance(token_types["input"], int):
            token_types["input"] = max(0, token_types["input"] - details["cached_tokens"])
    rates = _prices.get(model, {})
    for token_type, count in token_types.items():
        if type(count) is not int or count < 0:
            continue
        PROVIDER_TOKENS.labels(provider, model, purpose, token_type).inc(count)
    # Do not publish misleading partial cost if a reported token category lacks a rate.
    reported = {k: v for k, v in token_types.items() if type(v) is int and v > 0}
    if reported and all(k in rates and math.isfinite(rates[k]) and rates[k] >= 0 for k in reported):
        cost = sum(count * rates[k] for k, count in reported.items()) / 1_000_000
        PROVIDER_COST.labels(provider, model, purpose).inc(cost)
