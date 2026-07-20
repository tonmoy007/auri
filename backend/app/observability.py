"""Observability — Sentry error tracking and Prometheus metrics.

structlog already gives structured logs; this adds the two things it
can't: exception aggregation (Sentry) and request-latency/error-rate
metrics scraped by a monitoring system (Prometheus, via ``/metrics``).
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUEST_COUNT = Counter(
    "auri_http_requests_total",
    "Total HTTP requests processed",
    ["method", "path", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "auri_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
)


def init_sentry(dsn: str, environment: str) -> None:
    """Initialise Sentry error tracking.

    No-op if *dsn* is empty — local/dev environments and CI never set
    ``SENTRY_DSN``, so this must not require the sentry-sdk package to
    actually do anything to run the app.

    Args:
        dsn: Sentry project DSN. Empty string disables Sentry entirely.
        environment: Reported as the Sentry ``environment`` tag.
    """
    if not dsn:
        return

    import sentry_sdk

    sentry_sdk.init(dsn=dsn, environment=environment, traces_sample_rate=0.1)


async def _metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Record request count and latency for every HTTP request."""
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    path = request.url.path
    REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(duration)
    return response


def mount_metrics(app: FastAPI) -> None:
    """Register the Prometheus ``/metrics`` route and request-timing middleware.

    A plain route rather than ``prometheus_client.make_asgi_app()`` mounted
    as a sub-application — mounting redirects a bare ``GET /metrics`` (no
    trailing slash) to ``/metrics/`` with a 307, which not every scrape
    config follows.
    """
    app.middleware("http")(_metrics_middleware)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
