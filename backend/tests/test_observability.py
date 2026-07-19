"""Tests for app.observability — Sentry init and Prometheus /metrics.

Only the sentry_sdk boundary is mocked (AGENTS.md §16.4); the /metrics
endpoint and middleware run for real against the actual app.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.observability import init_sentry


def test_init_sentry_is_a_noop_when_dsn_is_empty() -> None:
    # Act / Assert — must not raise even though sentry_sdk is never touched
    init_sentry("", "development")


def test_init_sentry_calls_sentry_sdk_init_when_dsn_is_set() -> None:
    # Act
    with patch("sentry_sdk.init") as mock_init:
        init_sentry("https://examplePublicKey@o0.ingest.sentry.io/0", "production")

    # Assert
    mock_init.assert_called_once()
    _, kwargs = mock_init.call_args
    assert kwargs["dsn"] == "https://examplePublicKey@o0.ingest.sentry.io/0"
    assert kwargs["environment"] == "production"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text_format(
    client: AsyncClient,
) -> None:
    # Act
    response = await client.get("/metrics")

    # Assert
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_records_request_count_and_latency(
    client: AsyncClient,
) -> None:
    # Arrange — a request whose count/latency the middleware should record
    await client.get("/health")

    # Act
    response = await client.get("/metrics")

    # Assert
    body = response.text
    assert 'auri_http_requests_total{method="GET",path="/health"' in body
    assert "auri_http_request_duration_seconds" in body
