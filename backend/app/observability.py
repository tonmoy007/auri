"""Observability — Sentry error tracking and Prometheus metrics.

structlog already gives structured logs; this adds the two things it
can't: exception aggregation (Sentry) and request-latency/error-rate
metrics scraped by a monitoring system (Prometheus, via ``/metrics``).

``/metrics`` is a shared-secret endpoint and labels requests by matched route
template. It used to be open and to label the raw request path, which
published every confession id an HR user touched (and a label set any caller
could grow without bound) to anyone able to reach the port.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.config import settings

# Fixed labels for what a client controls freely. A path or method taken
# verbatim from the request would hand any caller unbounded label cardinality.
UNMATCHED_ROUTE_LABEL: Final = "unmatched"
OTHER_METHOD_LABEL: Final = "OTHER"
_TRACKED_METHODS: Final = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
)
_BEARER_SCHEME: Final = "bearer"

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

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=0.1,
        # A failed POST /confessions would otherwise ship the request body:
        # the raw transcript and the device hash.
        send_default_pii=False,
        max_request_body_size="never",
    )


def _route_label(request: Request) -> str:
    """Return the matched route's template (``/confessions/{confession_id}``).

    A concrete path carries ids, so it is never used as a label. A request
    that matched no route gets one shared label instead of its own.

    Args:
        request: The request, after routing has populated its scope.

    Returns:
        The route template, or ``UNMATCHED_ROUTE_LABEL``.
    """
    template = getattr(request.scope.get("route"), "path", None)
    if isinstance(template, str) and template:
        return template
    return UNMATCHED_ROUTE_LABEL


def _method_label(request: Request) -> str:
    """Return the HTTP method, or a shared label for one we do not serve.

    Args:
        request: The incoming request.

    Returns:
        The upper-cased method if it is a standard one, else ``OTHER_METHOD_LABEL``.
    """
    method = request.method.upper()
    return method if method in _TRACKED_METHODS else OTHER_METHOD_LABEL


async def _metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Record request count and latency for every HTTP request."""
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    # Read after call_next: routing happens downstream and only then does the
    # scope name the route that handled the request.
    method, route = _method_label(request), _route_label(request)
    REQUEST_COUNT.labels(method, route, response.status_code).inc()
    REQUEST_LATENCY.labels(method, route).observe(duration)
    return response


def _presented_token(authorization: str | None) -> bytes:
    """Return the bearer token from an ``Authorization`` header, as bytes.

    Bytes rather than ``str`` so a non-ASCII header value compares as a plain
    mismatch instead of raising ``TypeError`` inside ``compare_digest``. The
    scheme name is case-insensitive (RFC 7235).

    Args:
        authorization: Raw ``Authorization`` header value, if any.

    Returns:
        The token, or ``b""`` when the header is absent or not a bearer one.
    """
    if not authorization:
        return b""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != _BEARER_SCHEME:
        return b""
    return token.strip().encode()


async def require_metrics_access(authorization: str | None = Header(None)) -> None:
    """Allow only a scrape job presenting ``METRICS_API_KEY`` as a bearer token.

    Fails **closed**: an unset key (a missed deploy step) serves nothing
    rather than leaving the endpoint open. Bearer is used because it is what a
    Prometheus scrape job can send natively (``authorization.credentials``).

    Args:
        authorization: Raw ``Authorization`` header value, if any.

    Raises:
        HTTPException: 403 if no key is configured, 401 if the token is
            missing or wrong.
    """
    # Stripped like the presented token, so a key loaded from a secrets file
    # with a trailing newline does not lock the scraper out for good.
    expected = settings.METRICS_API_KEY.strip().encode()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Metrics are disabled until METRICS_API_KEY is configured",
        )
    if not secrets.compare_digest(_presented_token(authorization), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def mount_metrics(app: FastAPI) -> None:
    """Register the Prometheus ``/metrics`` route and request-timing middleware.

    A plain route rather than ``prometheus_client.make_asgi_app()`` mounted
    as a sub-application — mounting redirects a bare ``GET /metrics`` (no
    trailing slash) to ``/metrics/`` with a 307, which not every scrape
    config follows. The route requires ``METRICS_API_KEY`` as a bearer token
    (see :func:`require_metrics_access`).

    Args:
        app: The application to instrument.
    """
    app.middleware("http")(_metrics_middleware)

    @app.get(
        "/metrics",
        include_in_schema=False,
        dependencies=[Depends(require_metrics_access)],
    )
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
