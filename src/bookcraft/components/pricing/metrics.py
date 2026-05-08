from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import ClassVar

try:
    from prometheus_client import Counter, Gauge, Histogram
except Exception:  # pragma: no cover - prometheus is optional in embedded usage
    Counter = Histogram = Gauge = None  # type: ignore[misc, assignment]


class PricingMetrics:
    """Prometheus metrics wrapper with process-wide collector reuse.

    prometheus_client registers metrics globally by name. Tests and short-lived app factories may
    instantiate the engine multiple times, so this wrapper reuses collectors after the first init.
    """

    _shared: ClassVar[dict[str, object] | None] = None

    def __init__(self) -> None:
        if Counter is None:
            self.enabled = False
            return
        self.enabled = True
        if PricingMetrics._shared is None:
            PricingMetrics._shared = {
                "quote_requests": Counter(
                    "pricing_quote_requests_total",
                    "Total pricing quote requests.",
                    ["service", "status"],
                ),
                "quote_duration": Histogram(
                    "pricing_quote_duration_seconds",
                    "Pricing quote latency in seconds.",
                    ["service"],
                ),
                "quote_latency": Histogram(
                    "pricing_quote_latency_seconds",
                    "Pricing quote latency in seconds.",
                    ["service"],
                ),
                "quotes_total": Counter(
                    "pricing_quotes_total",
                    "Total pricing quotes by status.",
                    ["service", "status"],
                ),
                "quote_failures": Counter(
                    "pricing_quote_failures_total",
                    "Pricing quote failures by reason.",
                    ["service", "reason"],
                ),
                "quote_value": Histogram(
                    "pricing_quote_value_usd",
                    "Quote midpoint value in USD.",
                    ["service", "quote_mode"],
                ),
                "missing_inputs": Counter(
                    "pricing_missing_inputs_total",
                    "Missing input count by service and field.",
                    ["service", "field"],
                ),
                "human_review": Counter(
                    "pricing_human_review_total",
                    "Human review flags by service and reason.",
                    ["service", "reason"],
                ),
                "range_width": Gauge(
                    "pricing_quote_range_width",
                    "Latest quote range width ratio by service.",
                    ["service"],
                ),
            }
        self.quote_requests = PricingMetrics._shared["quote_requests"]
        self.quote_duration = PricingMetrics._shared["quote_duration"]
        self.quote_latency = PricingMetrics._shared["quote_latency"]
        self.quotes_total = PricingMetrics._shared["quotes_total"]
        self.quote_failures = PricingMetrics._shared["quote_failures"]
        self.quote_value = PricingMetrics._shared["quote_value"]
        self.missing_inputs = PricingMetrics._shared["missing_inputs"]
        self.human_review = PricingMetrics._shared["human_review"]
        self.range_width = PricingMetrics._shared["range_width"]

    @contextmanager
    def latency(self, service: str) -> Iterator[None]:
        start = perf_counter()
        try:
            yield
        finally:
            if self.enabled:
                self.quote_duration.labels(service=service).observe(perf_counter() - start)  # type: ignore[attr-defined]
                self.quote_latency.labels(service=service).observe(perf_counter() - start)  # type: ignore[attr-defined]

    def record_status(self, service: str, status: str) -> None:
        if self.enabled:
            self.quote_requests.labels(service=service, status=status).inc()  # type: ignore[attr-defined]
            self.quotes_total.labels(service=service, status=status).inc()  # type: ignore[attr-defined]
            if status in {"error", "failed"}:
                self.quote_failures.labels(service=service, reason=status).inc()  # type: ignore[attr-defined]

    def record_missing(self, service: str, field: str) -> None:
        if self.enabled:
            self.missing_inputs.labels(service=service, field=field).inc()  # type: ignore[attr-defined]

    def record_review(self, service: str, reason: str) -> None:
        if self.enabled:
            self.human_review.labels(service=service, reason=reason).inc()  # type: ignore[attr-defined]

    def record_value(self, service: str, quote_mode: str, midpoint: float) -> None:
        if self.enabled:
            self.quote_value.labels(service=service, quote_mode=quote_mode).observe(midpoint)  # type: ignore[attr-defined]

    def record_range_width(self, service: str, ratio: float) -> None:
        if self.enabled:
            self.range_width.labels(service=service).set(ratio)  # type: ignore[attr-defined]
