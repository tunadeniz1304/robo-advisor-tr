"""Prometheus metrics (process-wide registry).

Metrics are module level singletons so repeated ``create_app`` calls in tests
do not register duplicates. ``/metrics`` exposes them in the text format.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "advisor_http_requests_total",
    "HTTP istek sayısı",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "advisor_http_request_duration_seconds",
    "HTTP istek süresi (sn)",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
OPTIMIZER_SECONDS = Histogram(
    "advisor_optimizer_duration_seconds",
    "Optimizasyon süresi (sn)",
    ["method"],
    buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
    registry=REGISTRY,
)
MONTE_CARLO_SECONDS = Histogram(
    "advisor_monte_carlo_duration_seconds",
    "Monte Carlo simülasyon süresi (sn)",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2),
    registry=REGISTRY,
)
REBALANCE_TOTAL = Counter(
    "advisor_rebalance_events_total",
    "Yeniden dengeleme olayları",
    ["event"],  # proposed | approved | executed | rejected | expired
    registry=REGISTRY,
)
LLM_CALLS = Counter(
    "advisor_llm_calls_total",
    "LLM çağrıları",
    ["purpose", "mode", "error_kind"],
    registry=REGISTRY,
)
LLM_USAGE = Counter("advisor_llm_tokens_total", "LLM token kullanımı", ["mode"], registry=REGISTRY)
LLM_LATENCY = Histogram(
    "advisor_llm_latency_seconds",
    "LLM çağrı gecikmesi (sn)",
    ["mode"],
    buckets=(0.05, 0.1, 0.5, 1, 2, 5, 10, 20, 30),
    registry=REGISTRY,
)
DATA_SOURCE = Gauge(
    "advisor_market_data_live",
    "Son piyasa verisi canlı kaynaktan mı (1) yoksa snapshot'tan mı (0)",
    registry=REGISTRY,
)


def record_llm_call(
    purpose: str, mode: str, latency_ms: float, tokens: int, error_kind: str | None
) -> None:
    """Record one LLM call in the Prometheus metrics."""
    LLM_CALLS.labels(purpose=purpose, mode=mode, error_kind=error_kind or "none").inc()
    if tokens:
        LLM_USAGE.labels(mode=mode).inc(tokens)
    LLM_LATENCY.labels(mode=mode).observe(max(latency_ms, 0.0) / 1000.0)


def render_metrics() -> tuple[bytes, str]:
    """Return the exposition payload and its content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


__all__ = [
    "DATA_SOURCE",
    "HTTP_LATENCY",
    "HTTP_REQUESTS",
    "LLM_CALLS",
    "MONTE_CARLO_SECONDS",
    "OPTIMIZER_SECONDS",
    "REBALANCE_TOTAL",
    "REGISTRY",
    "record_llm_call",
    "render_metrics",
]
