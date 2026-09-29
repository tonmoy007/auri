"""Tests for app.observability — Sentry init and Prometheus /metrics.

Only the sentry_sdk boundary is mocked (AGENTS.md §16.4); the /metrics
endpoint, its authentication and the request-labelling middleware run for
real against the actual app.

``/metrics`` is a shared-secret endpoint: it used to be open and to label
every request with its raw path, which published per-confession UUIDs (and an
unbounded label set) to anyone who could reach the port.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from app.observability import (
    OTHER_METHOD_LABEL,
    UNMATCHED_ROUTE_LABEL,
    init_sentry,
)
from httpx import AsyncClient
from prometheus_client import REGISTRY

from tests.conftest import SettingPatcher

METRICS_KEY = "scrape-key-for-tests-only"
METRICS_HEADERS = {"Authorization": f"Bearer {METRICS_KEY}"}
STAFF_REPLY_TEMPLATE = "/api/v1/hr/confessions/{confession_id}/reply"
CONFESSION_TEMPLATE = "/api/v1/confessions/{confession_id}"


def _request_count(method: str, route: str, status_code: int) -> float:
    """Current value of one request-counter series, 0 if it has no samples yet.

    The registry is process-global, so tests assert on the change their own
    request caused rather than on a series another test may have created.
    """
    return (
        REGISTRY.get_sample_value(
            "auri_http_requests_total",
            {"method": method, "path": route, "status_code": str(status_code)},
        )
        or 0.0
    )


def _latency_count(method: str, route: str) -> float:
    """Current number of latency observations for one method and route."""
    return (
        REGISTRY.get_sample_value(
            "auri_http_request_duration_seconds_count",
            {"method": method, "path": route},
        )
        or 0.0
    )


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
    # Request bodies carry the raw transcript and device hash on a failed submit
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["send_default_pii"] is False


# ── Authentication ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer "}, METRICS_HEADERS],
    ids=["no-header", "empty-token", "any-token"],
)
async def test_metrics_is_denied_when_no_key_is_configured(
    api_client: AsyncClient, set_setting: SettingPatcher, headers: dict[str, str]
) -> None:
    # Arrange — a missed deploy step must close the endpoint, not open it. The
    # empty cases matter: "" == "" would otherwise read as a matching secret.
    set_setting("METRICS_API_KEY", "")

    # Act
    response = await api_client.get("/metrics", headers=headers)

    # Assert
    assert response.status_code == 403
    assert "auri_http_requests_total" not in response.text


@pytest.mark.asyncio
async def test_metrics_rejects_a_request_with_no_credentials(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("METRICS_API_KEY", METRICS_KEY)

    # Act
    response = await api_client.get("/metrics")

    # Assert
    assert response.status_code == 401
    assert "auri_http_requests_total" not in response.text


@pytest.mark.asyncio
async def test_metrics_rejects_the_wrong_token(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("METRICS_API_KEY", METRICS_KEY)

    # Act
    response = await api_client.get(
        "/metrics", headers={"Authorization": "Bearer not-the-key"}
    )

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_metrics_rejects_the_key_sent_without_the_bearer_scheme(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("METRICS_API_KEY", METRICS_KEY)

    # Act
    response = await api_client.get("/metrics", headers={"Authorization": METRICS_KEY})

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_metrics_answers_a_non_ascii_token_with_401_not_a_server_error(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange — compare_digest raises TypeError on non-ASCII str input
    set_setting("METRICS_API_KEY", METRICS_KEY)

    # Act
    response = await api_client.get(
        "/metrics",
        headers=[(b"authorization", "Bearer pässwörd".encode("latin-1"))],
    )

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_metrics_serves_prometheus_text_to_the_right_token(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("METRICS_API_KEY", METRICS_KEY)

    # Act
    response = await api_client.get("/metrics", headers=METRICS_HEADERS)

    # Assert
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_accepts_the_bearer_scheme_in_any_letter_case(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange — RFC 7235: the scheme name is case-insensitive
    set_setting("METRICS_API_KEY", METRICS_KEY)

    # Act
    response = await api_client.get(
        "/metrics", headers={"Authorization": f"bEaReR {METRICS_KEY}"}
    )

    # Assert
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_metrics_ignores_whitespace_around_the_configured_key(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange — a key read from a secrets file usually ends in a newline
    set_setting("METRICS_API_KEY", f"{METRICS_KEY}\n")

    # Act
    response = await api_client.get("/metrics", headers=METRICS_HEADERS)

    # Assert
    assert response.status_code == 200


# ── Labelling ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_records_request_count_and_latency(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("METRICS_API_KEY", METRICS_KEY)
    count_before = _request_count("GET", "/health", 200)
    latency_before = _latency_count("GET", "/health")

    # Act
    await api_client.get("/health")

    # Assert
    assert _request_count("GET", "/health", 200) == count_before + 1
    assert _latency_count("GET", "/health") == latency_before + 1


@pytest.mark.asyncio
async def test_metrics_label_a_confession_route_by_its_template_not_its_id(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("METRICS_API_KEY", METRICS_KEY)
    confession_id = str(uuid.uuid4())
    before = _request_count("GET", CONFESSION_TEMPLATE, 404)

    # Act
    await api_client.get(
        f"/api/v1/confessions/{confession_id}",
        headers={"X-Device-Token-Hash": "a" * 32},
    )
    body = (await api_client.get("/metrics", headers=METRICS_HEADERS)).text

    # Assert
    assert _request_count("GET", CONFESSION_TEMPLATE, 404) == before + 1
    assert confession_id not in body


@pytest.mark.asyncio
async def test_metrics_label_a_staff_route_by_its_template_not_its_id(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange — the route HR writes replies through; refused (401) requests
    # are recorded too, and must not name the confession either
    set_setting("METRICS_API_KEY", METRICS_KEY)
    confession_id = str(uuid.uuid4())
    before = _request_count("PUT", STAFF_REPLY_TEMPLATE, 401)

    # Act
    await api_client.put(
        f"/api/v1/hr/confessions/{confession_id}/reply", json={"reply": "hello"}
    )
    body = (await api_client.get("/metrics", headers=METRICS_HEADERS)).text

    # Assert
    assert _request_count("PUT", STAFF_REPLY_TEMPLATE, 401) == before + 1
    assert confession_id not in body


@pytest.mark.asyncio
async def test_metrics_fold_every_unmatched_path_into_one_label(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange — a scanner probing random URLs must not grow the label set
    set_setting("METRICS_API_KEY", METRICS_KEY)
    probe = f"probe-{uuid.uuid4()}"
    before = _request_count("GET", UNMATCHED_ROUTE_LABEL, 404)

    # Act
    await api_client.get(f"/no-such-route/{probe}")
    body = (await api_client.get("/metrics", headers=METRICS_HEADERS)).text

    # Assert
    assert _request_count("GET", UNMATCHED_ROUTE_LABEL, 404) == before + 1
    assert probe not in body


@pytest.mark.asyncio
async def test_metrics_fold_an_unknown_http_method_into_one_label(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange — method tokens are client-chosen, so they are unbounded too
    set_setting("METRICS_API_KEY", METRICS_KEY)
    before = _request_count(OTHER_METHOD_LABEL, "/health", 405)

    # Act
    await api_client.request("BREWCOFFEE", "/health")
    body = (await api_client.get("/metrics", headers=METRICS_HEADERS)).text

    # Assert
    assert _request_count(OTHER_METHOD_LABEL, "/health", 405) == before + 1
    assert "BREWCOFFEE" not in body


def test_sql_echo_is_off_by_default_even_in_development() -> None:
    # Arrange
    from app.config import Settings
    from app.database import engine

    # Act
    configured = Settings(_env_file=None, ENVIRONMENT="development")

    # Assert — echo logs INSERT parameters: transcripts, summaries, reply text
    assert configured.SQL_ECHO is False
    assert engine.echo is False
