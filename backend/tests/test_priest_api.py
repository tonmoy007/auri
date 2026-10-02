"""Tests for the public priest routes: ``GET /priest/status`` and ``POST /priest/ask``.

The routes have no staff auth: the device header is the identity. They must check the
kill switch before anything else, refuse a device that asks too fast, and never read,
store, log or echo a question or a device code. The orchestrator is always replaced by
a fake through ``app.dependency_overrides``; nothing here touches a model or the vault.
"""

from __future__ import annotations

import logging
import sys
import types
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from app.database import get_async_session
from app.exceptions import PriestIndexError, PriestUnavailableError
from app.main import create_app
from app.priest import metrics
from app.priest.rate_limiter import FIXED_REPLY_DEVICE_LIMITS, PriestRateLimiter
from app.priest.schemas import (
    AnswerKind,
    CrisisContactOut,
    PriestAnswerResponse,
    PriestCitation,
    PriestPoint,
    PriestQuote,
    TraditionId,
)
from app.services import settings_service
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import SettingPatcher
from tests.route_listing import flattened_routes

try:
    import app.priest.priest_service  # noqa: F401
except ModuleNotFoundError:  # the orchestrator lane may not have landed yet
    _stub = types.ModuleType("app.priest.priest_service")

    class _StubPriestService:
        """Placeholder so the router imports; every test overrides the dependency."""

        async def answer(self, *args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError

    def _stub_get_priest_service() -> _StubPriestService:
        return _StubPriestService()

    _stub.PriestService = _StubPriestService  # type: ignore[attr-defined]
    _stub.get_priest_service = _stub_get_priest_service  # type: ignore[attr-defined]
    sys.modules["app.priest.priest_service"] = _stub

from app.api.v1 import priest as priest_module
from app.api.v1.priest import router as priest_router
from app.priest.priest_service import get_priest_service
from app.priest.rate_limiter import get_rate_limiter

ASK = "/api/v1/priest/ask"
STATUS = "/api/v1/priest/status"
DEVICE = "DEVICECODE-0123456789abcdef-unique"
QUESTION = "What do the notes say about QUESTIONMARKER patience?"
DEVICE_HEADER = {"X-Device-Token-Hash": DEVICE}


class FakeService:
    """Stands in for ``PriestService``; records calls, returns or raises on demand."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, TraditionId | None, str]] = []
        self.kind = AnswerKind.answer
        self.error: Exception | None = None

    async def answer(
        self, question: str, tradition: TraditionId | None, *, request_id: str
    ) -> PriestAnswerResponse:
        self.calls.append((question, tradition, request_id))
        if self.error is not None:
            raise self.error
        return _response(self.kind, request_id)


def _response(kind: AnswerKind, request_id: str) -> PriestAnswerResponse:
    """A response of *kind* with the fields that kind uses filled in."""
    citation = PriestCitation(
        id="S1",
        note_title="Patience in the library",
        heading_path=["Virtues", "Patience"],
        note_type="concept",
        tradition_labels=["Buddhism"],
        snippet="A short synthetic snippet.",
    )
    if kind is AnswerKind.answer:
        return PriestAnswerResponse(
            request_id=request_id,
            kind=kind,
            points=[PriestPoint(text="A synthetic point.", citation_ids=["S1"])],
            quotes=[
                PriestQuote(text="a synthetic quote", citation_id="S1", label="Text")
            ],
            reflection="A synthetic reflection.",
            citations=[citation],
            index_version="v1",
            prompt_version="1.0",
        )
    if kind is AnswerKind.crisis:
        return PriestAnswerResponse(
            request_id=request_id,
            kind=kind,
            notice="Fixed crisis text.",
            contacts=[CrisisContactOut(label="Line", detail="Call", dial="+10000000")],
        )
    if kind is AnswerKind.library_excerpts:
        return PriestAnswerResponse(
            request_id=request_id,
            kind=kind,
            citations=[citation],
            notice="Excerpts only.",
        )
    return PriestAnswerResponse(request_id=request_id, kind=kind, notice="Fixed text.")


class Env:
    """The pieces one test needs: client, fake service, limiter, resolution count."""

    def __init__(
        self,
        client: AsyncClient,
        service: FakeService,
        limiter: PriestRateLimiter,
        app: FastAPI,
    ) -> None:
        self.client = client
        self.service = service
        self.limiter = limiter
        self.app = app
        self.service_resolutions = 0

    async def ask(
        self, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        """POST a question; both default to a valid request."""
        return await self.client.post(
            ASK,
            json=body if body is not None else {"question": QUESTION},
            headers=DEVICE_HEADER if headers is None else headers,
        )


@pytest_asyncio.fixture
async def env(set_setting: SettingPatcher) -> AsyncIterator[Env]:
    """A fresh app with the priest router, a fake service and a fresh limiter."""
    app = create_app()
    if not any(getattr(r, "path", None) == ASK for r in flattened_routes(app)):
        app.include_router(priest_router, prefix="/api/v1")
    service = FakeService()
    limiter = PriestRateLimiter()
    metrics.reset()
    set_setting("PRIEST_MODE_ENABLED", True)
    set_setting("PRIEST_RATE_LIMIT_PER_MINUTE", 4)
    set_setting("PRIEST_RATE_LIMIT_PER_DAY", 40)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        holder = Env(client, service, limiter, app)

        def provide_service() -> FakeService:
            holder.service_resolutions += 1
            return service

        app.dependency_overrides[get_priest_service] = provide_service
        app.dependency_overrides[get_rate_limiter] = lambda: limiter
        yield holder
    app.dependency_overrides.clear()


def _outcomes() -> dict[str, int]:
    return metrics.snapshot().outcomes


# ── POST /ask: the kill switch ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_gives_503_with_no_service_call(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_MODE_ENABLED", False)

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 503
    assert response.json() == {"detail": "priest_mode_disabled"}
    assert env.service.calls == []
    assert _outcomes() == {"disabled": 1}


@pytest.mark.asyncio
async def test_disabled_never_resolves_the_service(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange: resolving the real service may load the index
    set_setting("PRIEST_MODE_ENABLED", False)

    # Act
    await env.ask()

    # Assert
    assert env.service_resolutions == 0


@pytest.mark.asyncio
async def test_disabled_does_not_use_up_the_device_quota(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_MODE_ENABLED", False)
    for _ in range(6):
        await env.ask()
    set_setting("PRIEST_MODE_ENABLED", True)

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 200


# ── POST /ask: the device header ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_header_gives_422(env: Env) -> None:
    # Arrange / Act
    response = await env.client.post(ASK, json={"question": QUESTION})

    # Assert
    assert response.status_code == 422
    assert env.service.calls == []


@pytest.mark.asyncio
async def test_header_under_16_characters_gives_422(env: Env) -> None:
    # Arrange / Act
    response = await env.ask(headers={"X-Device-Token-Hash": "a" * 15})

    # Assert
    assert response.status_code == 422
    assert env.service.calls == []


@pytest.mark.asyncio
async def test_header_over_256_characters_gives_422(env: Env) -> None:
    # Arrange / Act
    response = await env.ask(headers={"X-Device-Token-Hash": "a" * 257})

    # Assert
    assert response.status_code == 422


@pytest.mark.parametrize("length", [16, 256])
@pytest.mark.asyncio
async def test_header_at_the_bounds_is_accepted(env: Env, length: int) -> None:
    # Arrange / Act
    response = await env.ask(headers={"X-Device-Token-Hash": "a" * length})

    # Assert
    assert response.status_code == 200


# ── POST /ask: the body ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "extra",
    [
        {"confession_id": str(uuid.uuid4())},
        {"device_token_hash": DEVICE},
        {"anything": "else"},
    ],
)
@pytest.mark.asyncio
async def test_extra_body_field_gives_422(env: Env, extra: dict[str, Any]) -> None:
    # Arrange
    body = {"question": QUESTION, **extra}

    # Act
    response = await env.ask(body)

    # Assert
    assert response.status_code == 422
    assert env.service.calls == []


@pytest.mark.asyncio
async def test_too_short_question_gives_422(env: Env) -> None:
    # Arrange / Act
    response = await env.ask({"question": "hi"})

    # Assert
    assert response.status_code == 422
    assert env.service.calls == []


@pytest.mark.asyncio
async def test_invalid_requests_do_not_use_up_the_device_quota(env: Env) -> None:
    # Arrange
    for _ in range(6):
        await env.ask({"question": "hi"})

    # Act
    responses = [await env.ask() for _ in range(4)]

    # Assert
    assert [r.status_code for r in responses] == [200, 200, 200, 200]


@pytest.mark.asyncio
async def test_question_and_tradition_reach_the_service(env: Env) -> None:
    # Arrange
    body = {"question": f"  {QUESTION}  ", "tradition": "islam", "language": "en"}

    # Act
    response = await env.ask(body)

    # Assert
    assert response.status_code == 200
    question, tradition, request_id = env.service.calls[0]
    assert question == QUESTION
    assert tradition is TraditionId.islam
    assert response.json()["request_id"] == request_id


@pytest.mark.asyncio
async def test_request_id_is_a_fresh_random_uuid4(env: Env) -> None:
    # Arrange / Act
    first = await env.ask()
    second = await env.ask()

    # Assert
    ids = [first.json()["request_id"], second.json()["request_id"]]
    assert ids[0] != ids[1]
    for value in ids:
        assert uuid.UUID(value).version == 4
        assert DEVICE not in value


# ── POST /ask: the rate limit ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fifth_request_in_a_minute_gives_429_with_integer_retry_after(
    env: Env,
) -> None:
    # Arrange
    for _ in range(4):
        assert (await env.ask()).status_code == 200

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 429
    assert response.json() == {"detail": "rate_limited"}
    retry_after = response.headers["Retry-After"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60
    assert len(env.service.calls) == 4
    assert _outcomes()["rate_limited"] == 1


@pytest.mark.asyncio
async def test_daily_limit_gives_429_with_a_long_retry_after(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_RATE_LIMIT_PER_MINUTE", 100)
    set_setting("PRIEST_RATE_LIMIT_PER_DAY", 3)
    for _ in range(3):
        assert (await env.ask()).status_code == 200

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 429
    assert response.headers["Retry-After"].isdigit()
    assert int(response.headers["Retry-After"]) > 3600


@pytest.mark.asyncio
async def test_another_device_is_not_limited(env: Env) -> None:
    # Arrange
    for _ in range(4):
        await env.ask()

    # Act
    response = await env.ask(headers={"X-Device-Token-Hash": "b" * 32})

    # Assert
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_error_keeps_confession_handler_working(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange: the generic RateLimitError handler still maps to 429 with its text
    from app.exceptions import RateLimitError

    @env.app.get("/__test_generic_rate_limit")
    async def _boom() -> None:
        raise RateLimitError("rate limit exceeded; retry in 7s")

    # Act
    response = await env.client.get("/__test_generic_rate_limit")

    # Assert
    assert response.status_code == 429
    assert response.json() == {"detail": "rate limit exceeded; retry in 7s"}
    assert "Retry-After" not in response.headers


# ── POST /ask: every answer kind ─────────────────────────────────────────


@pytest.mark.parametrize("kind", list(AnswerKind))
@pytest.mark.asyncio
async def test_every_answer_kind_serialises(env: Env, kind: AnswerKind) -> None:
    # Arrange
    env.service.kind = kind

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == kind.value
    assert body["request_id"] == env.service.calls[0][2]
    assert body["disclaimer_version"] == "1"
    assert set(body) >= {"points", "quotes", "citations", "notice", "contacts"}


@pytest.mark.asyncio
async def test_crisis_contacts_and_answer_content_are_passed_through(
    env: Env,
) -> None:
    # Arrange
    env.service.kind = AnswerKind.crisis

    # Act
    crisis = (await env.ask()).json()
    env.service.kind = AnswerKind.answer
    answer = (await env.ask()).json()

    # Assert
    assert crisis["contacts"][0]["dial"] == "+10000000"
    assert crisis["notice"] == "Fixed crisis text."
    assert answer["points"][0]["citation_ids"] == ["S1"]
    assert answer["quotes"][0]["label"] == "Text"
    assert answer["citations"][0]["heading_path"] == ["Virtues", "Patience"]


# ── POST /ask: failures from the service ─────────────────────────────────


@pytest.mark.asyncio
async def test_busy_gives_503_with_retry_after(env: Env) -> None:
    # Arrange
    env.service.error = PriestUnavailableError("priest_busy", retry_after=3)

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 503
    assert response.json() == {"detail": "priest_busy"}
    assert response.headers["Retry-After"] == "3"
    # the service already counted it; the route must not count it a second time
    assert _outcomes() == {}


@pytest.mark.asyncio
async def test_busy_without_a_hint_still_carries_retry_after(env: Env) -> None:
    # Arrange
    env.service.error = PriestUnavailableError("priest_busy")

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 503
    assert response.headers["Retry-After"].isdigit()


@pytest.mark.asyncio
async def test_index_unavailable_from_the_service_passes_through(env: Env) -> None:
    # Arrange
    env.service.error = PriestUnavailableError("priest_index_unavailable")

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 503
    assert response.json() == {"detail": "priest_index_unavailable"}
    assert _outcomes() == {}


@pytest.mark.asyncio
async def test_priest_error_maps_to_503_index_unavailable(env: Env) -> None:
    # Arrange: the message holds the question; it must not reach the client
    env.service.error = PriestIndexError(f"corrupt near {QUESTION}")

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 503
    assert response.json() == {"detail": "priest_index_unavailable"}
    assert "QUESTIONMARKER" not in response.text
    assert _outcomes() == {"error": 1}


@pytest.mark.asyncio
async def test_unexpected_error_gives_a_fixed_500(env: Env) -> None:
    # Arrange
    env.service.error = RuntimeError(f"boom {QUESTION} {DEVICE}")

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 500
    assert response.json() == {"detail": "priest_unexpected_error"}
    assert _outcomes() == {"error": 1}


# ── Privacy: responses and logs ──────────────────────────────────────────


def _assert_no_leak(response: Any) -> None:
    blob = response.text + repr(dict(response.headers))
    assert DEVICE not in blob
    assert "QUESTIONMARKER" not in blob


@pytest.mark.parametrize(
    "scenario", ["success", "rate_limited", "disabled", "busy", "index", "unexpected"]
)
@pytest.mark.asyncio
async def test_response_never_contains_the_question_or_device_code(
    env: Env, set_setting: SettingPatcher, scenario: str
) -> None:
    # Arrange
    _prepare(env, set_setting, scenario)

    # Act
    response = await _drive(env, scenario)

    # Assert
    _assert_no_leak(response)


@pytest.mark.parametrize(
    "scenario", ["success", "rate_limited", "disabled", "busy", "index", "unexpected"]
)
@pytest.mark.asyncio
async def test_no_log_record_carries_the_question_or_device_code(
    env: Env,
    set_setting: SettingPatcher,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
) -> None:
    # Arrange
    caplog.set_level(logging.DEBUG)
    _prepare(env, set_setting, scenario)

    # Act
    await _drive(env, scenario)

    # Assert
    everything = " ".join(
        f"{r.getMessage()} {r.__dict__} {r.exc_text}" for r in caplog.records
    )
    assert "QUESTIONMARKER" not in everything
    assert DEVICE not in everything


@pytest.mark.asyncio
async def test_unexpected_error_logs_only_the_class_name_and_request_id(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange
    caplog.set_level(logging.DEBUG)
    env.service.error = RuntimeError(f"boom {QUESTION}")

    # Act
    await env.ask()

    # Assert
    text = caplog.text
    assert "RuntimeError" in text
    assert env.service.calls[0][2] in text
    assert "boom" not in text


def _prepare(env: Env, set_setting: SettingPatcher, scenario: str) -> None:
    if scenario == "disabled":
        set_setting("PRIEST_MODE_ENABLED", False)
    elif scenario == "rate_limited":
        set_setting("PRIEST_RATE_LIMIT_PER_MINUTE", 1)
    elif scenario == "busy":
        env.service.error = PriestUnavailableError("priest_busy", retry_after=2)
    elif scenario == "index":
        env.service.error = PriestIndexError(f"bad {QUESTION} {DEVICE}")
    elif scenario == "unexpected":
        env.service.error = RuntimeError(f"boom {QUESTION} {DEVICE}")


async def _drive(env: Env, scenario: str) -> Any:
    if scenario == "rate_limited":
        await env.ask()
    return await env.ask()


# ── No confessions, no staff auth ────────────────────────────────────────


def _walk_calls(dependant: Any) -> list[Callable[..., Any]]:
    found = [dependant.call] if dependant.call else []
    for sub in dependant.dependencies:
        found.extend(_walk_calls(sub))
    return found


def test_routes_use_no_database_session_and_no_staff_auth(env: Env) -> None:
    # Arrange
    routes = [r for r in priest_router.routes if hasattr(r, "dependant")]

    # Act
    calls = [c for r in routes for c in _walk_calls(r.dependant)]  # type: ignore[attr-defined]

    # Assert
    assert len(routes) == 2
    assert get_async_session not in calls
    assert not [c for c in calls if "auth" in getattr(c, "__module__", "")]


def test_module_does_not_touch_the_confessions_tables() -> None:
    # Arrange
    source = Path(priest_module.__file__).read_text(encoding="utf-8")

    # Act / Assert
    assert "models.confession" not in source
    assert "AsyncSession" not in source


# ── GET /status ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_works_without_a_header_and_is_cacheable(env: Env) -> None:
    # Arrange / Act
    response = await env.client.get(STATUS)

    # Assert
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "max-age=60"
    body = response.json()
    assert body["enabled"] is True
    assert body["persona_name"] == "Guide"
    assert body["disclaimer_version"] == "1"
    assert body["max_question_chars"] == 1000


@pytest.mark.asyncio
async def test_status_never_resolves_or_calls_the_service(env: Env) -> None:
    # Arrange / Act
    await env.client.get(STATUS)

    # Assert
    assert env.service_resolutions == 0
    assert env.service.calls == []


@pytest.mark.asyncio
async def test_status_lists_every_tradition_with_its_label_by_default(
    env: Env,
) -> None:
    # Arrange / Act
    body = (await env.client.get(STATUS)).json()

    # Assert
    assert len(body["traditions"]) == len(TraditionId)
    assert {"id": "egyptian", "label": "Egyptian religion"} in body["traditions"]
    assert {"id": "islam", "label": "Islam"} in body["traditions"]


@pytest.mark.asyncio
async def test_status_lists_only_enabled_traditions(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setitem(
        settings_service._cache, "PRIEST_TRADITIONS_ENABLED", '["buddhism", "islam"]'
    )

    # Act
    body = (await env.client.get(STATUS)).json()

    # Assert
    assert body["traditions"] == [
        {"id": "islam", "label": "Islam"},
        {"id": "buddhism", "label": "Buddhism"},
    ]


@pytest.mark.asyncio
async def test_status_reports_disabled_when_the_flag_is_off(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_MODE_ENABLED", False)

    # Act
    response = await env.client.get(STATUS)

    # Assert
    assert response.status_code == 200
    assert response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_status_uses_the_configured_persona_name(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setitem(settings_service._cache, "PRIEST_PERSONA_NAME", "Sage")

    # Act
    body = (await env.client.get(STATUS)).json()

    # Assert
    assert body["persona_name"] == "Sage"


# ── POST /ask: fixed replies are never rate-limited ──────────────────────────


@pytest.mark.asyncio
async def test_a_crisis_message_is_answered_after_the_limit_is_used_up(
    env: Env,
) -> None:
    # Arrange — a distressed person who rephrased four times and hit the limit
    for _ in range(4):
        assert (await env.ask()).status_code == 200
    assert (await env.ask()).status_code == 429

    # Act
    response = await env.ask({"question": "I want to kill myself"})

    # Assert — routed before the limiter, so help is never a 429
    assert response.status_code == 200
    assert env.service.calls[-1][0] == "I want to kill myself"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "I want to kill myself",
        "my manager harassed me",
        "नमस्ते",
    ],
)
async def test_fixed_replies_do_not_use_up_the_limit(env: Env, question: str) -> None:
    # Arrange
    for _ in range(10):
        assert (await env.ask({"question": question})).status_code == 200

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 200
    assert _outcomes().get("rate_limited", 0) == 0


# ── review fixes: status contacts and validation errors ──────────────────────


@pytest.mark.asyncio
async def test_status_carries_the_configured_crisis_contacts(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("CRISIS_HELPLINE_NAME", "Lifeline")
    set_setting("CRISIS_HELPLINE_NUMBER", "+880 1234-567890")

    # Act
    body = (await env.client.get(STATUS)).json()

    # Assert — the intro shows help before any question is asked
    assert body["crisis_contacts"] == [
        {"label": "Lifeline", "detail": "+880 1234-567890", "dial": "+8801234567890"}
    ]


@pytest.mark.asyncio
async def test_status_has_an_empty_contact_list_when_none_are_configured(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("CRISIS_HELPLINE_NAME", "")
    set_setting("CRISIS_HELPLINE_NUMBER", "")
    set_setting("CRISIS_EAP_CONTACT", "")

    # Act
    body = (await env.client.get(STATUS)).json()

    # Assert
    assert body["crisis_contacts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "headers"),
    [
        ({"question": "VALIDATIONMARKER " * 100}, DEVICE_HEADER),
        ({"question": "VALIDATIONMARKER\x00bad"}, DEVICE_HEADER),
        (
            {"question": QUESTION, "extra": "VALIDATIONMARKER"},
            DEVICE_HEADER,
        ),
        ({"question": QUESTION}, {"X-Device-Token-Hash": "SHORTMARKER"}),
    ],
)
async def test_a_validation_error_never_echoes_the_question_or_the_header(
    env: Env, body: dict[str, Any], headers: dict[str, str]
) -> None:
    # Act
    response = await env.ask(body, headers)

    # Assert — only where and what kind, never the value (a gateway may log bodies)
    assert response.status_code == 422
    assert "VALIDATIONMARKER" not in response.text
    assert "SHORTMARKER" not in response.text
    for item in response.json()["detail"]:
        assert set(item) == {"loc", "type"}


@pytest.mark.asyncio
async def test_a_refusal_by_the_real_service_is_counted_once_not_twice(
    env: Env,
) -> None:
    # Arrange — the fake service above records nothing, which hid a double count
    from tests.test_priest_service import FakeRetriever, make_service

    real = make_service(retriever=FakeRetriever(error=PriestIndexError("x")))
    env.app.dependency_overrides[get_priest_service] = lambda: real
    metrics.reset()

    # Act
    response = await env.ask()

    # Assert
    assert response.status_code == 503
    assert _outcomes() == {"error": 1}


# ── POST /ask: the per-address limit behind a trusted proxy (plan 14.7) ─────


def _device(n: int) -> str:
    return f"{n:032d}"


@pytest.mark.asyncio
async def test_a_client_varying_its_device_header_is_limited_by_address(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("TRUSTED_PROXY_HEADER", "X-Real-IP")
    set_setting("PRIEST_RATE_LIMIT_PER_IP_PER_MINUTE", 3)
    for n in range(3):
        headers = {"X-Device-Token-Hash": _device(n), "X-Real-IP": "203.0.113.7"}
        assert (await env.ask(headers=headers)).status_code == 200

    # Act
    response = await env.ask(
        headers={"X-Device-Token-Hash": _device(99), "X-Real-IP": "203.0.113.7"}
    )

    # Assert
    assert response.status_code == 429
    assert response.json() == {"detail": "rate_limited"}
    assert len(env.service.calls) == 3


@pytest.mark.asyncio
async def test_an_untrusted_forwarded_header_is_ignored(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange — the deployment trusts X-Real-IP; the client sends X-Forwarded-For
    set_setting("TRUSTED_PROXY_HEADER", "X-Real-IP")
    set_setting("PRIEST_RATE_LIMIT_PER_IP_PER_MINUTE", 1)

    # Act
    statuses = [
        (
            await env.ask(
                headers={
                    "X-Device-Token-Hash": _device(n),
                    "X-Forwarded-For": "203.0.113.7",
                }
            )
        ).status_code
        for n in range(3)
    ]

    # Assert — no address is known, so only the device limit applies
    assert statuses == [200, 200, 200]


@pytest.mark.asyncio
async def test_without_a_trusted_header_no_address_limit_runs(
    env: Env, set_setting: SettingPatcher
) -> None:
    # Arrange — the default: no proxy trusted
    set_setting("TRUSTED_PROXY_HEADER", "")
    set_setting("PRIEST_RATE_LIMIT_PER_IP_PER_MINUTE", 1)

    # Act
    statuses = [
        (
            await env.ask(
                headers={"X-Device-Token-Hash": _device(n), "X-Real-IP": "203.0.113.7"}
            )
        ).status_code
        for n in range(3)
    ]

    # Assert
    assert statuses == [200, 200, 200]


@pytest.mark.asyncio
async def test_fixed_replies_stop_at_their_flood_ceiling(env: Env) -> None:
    # Arrange
    crisis = {"question": "I want to kill myself"}
    for _ in range(FIXED_REPLY_DEVICE_LIMITS.per_minute):
        assert (await env.ask(crisis)).status_code == 200

    # Act
    response = await env.ask(crisis)

    # Assert
    assert response.status_code == 429
    assert response.json() == {"detail": "rate_limited"}
