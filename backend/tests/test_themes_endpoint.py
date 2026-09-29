"""Tests for the OpenAI-compatible theme model: its address rules and its client.

The endpoint receives de-identified summaries and an API key, so the rules that
decide whether it may be used are tested at their edges, and the client is
exercised against an in-process transport (httpx.MockTransport) so the real
request-building code runs without any network (AGENTS.md §16.4).
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

import httpx
import pytest
from app.exceptions import ThemesEndpointError
from app.services.openai_compatible import (
    MAX_REPLY_TOKENS,
    MAX_RESPONSE_BYTES,
    chat_complete,
    strip_reasoning,
)
from app.services.themes_endpoint import (
    ThemesEndpoint,
    is_local_host,
    resolve_endpoint,
)

from tests.conftest import SettingPatcher


@pytest.fixture(autouse=True)
def blank_endpoint_settings(set_setting: SettingPatcher) -> None:
    """Start every test with no themes endpoint and no keys, whatever .env holds."""
    for name, value in (
        ("THEMES_LLM_BASE_URL", ""),
        ("THEMES_LLM_MODEL", ""),
        ("THEMES_LLM_API_KEY", ""),
        ("THEMES_LLM_ALLOW_INSECURE_HTTP", False),
        ("THEMES_LLM_USE_OPENAI_API_KEY", False),
        ("OPENAI_API_KEY", ""),
    ):
        set_setting(name, value)


def _configure(
    set_setting: SettingPatcher, url: str, model: str = "qwen3.5-9b"
) -> None:
    set_setting("THEMES_LLM_BASE_URL", url)
    set_setting("THEMES_LLM_MODEL", model)


def test_no_base_url_means_the_local_model_is_used() -> None:
    # Act / Assert
    assert resolve_endpoint() is None


@pytest.mark.parametrize(
    "url",
    [
        "https://models.example.net",
        "https://models.example.net/",
        "https://models.example.net/v1",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://10.0.0.5:8000",
        "http://192.168.1.9:8000",
        "http://172.20.0.4:8000",
    ],
)
def test_https_or_a_private_address_is_accepted(
    url: str, set_setting: SettingPatcher
) -> None:
    # Arrange
    _configure(set_setting, url)

    # Act
    endpoint = resolve_endpoint()

    # Assert
    assert endpoint is not None
    assert endpoint.model == "qwen3.5-9b"
    assert not endpoint.base_url.endswith("/")
    assert not endpoint.base_url.endswith("/v1")


@pytest.mark.parametrize(
    "url",
    [
        "http://118.67.212.45:8000",
        "http://models.example.net",
        "http://203.0.113.9:8000",
    ],
)
def test_plain_http_to_a_public_address_is_refused_by_default(
    url: str, set_setting: SettingPatcher
) -> None:
    # Arrange — the key and the summaries would cross the internet unencrypted
    _configure(set_setting, url)

    # Act / Assert
    with pytest.raises(ThemesEndpointError, match="unencrypted"):
        resolve_endpoint()


def test_plain_http_to_a_public_address_needs_an_explicit_opt_in(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting, "http://118.67.212.45:8000")
    set_setting("THEMES_LLM_ALLOW_INSECURE_HTTP", True)

    # Act
    endpoint = resolve_endpoint()

    # Assert
    assert endpoint is not None
    assert endpoint.host == "118.67.212.45"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://models.example.net",
        "not a url",
        "https://",
        "https://user:pw@models.example.net",
    ],
)
def test_a_malformed_or_credentialed_address_is_refused(
    url: str, set_setting: SettingPatcher
) -> None:
    # Arrange
    _configure(set_setting, url)

    # Act / Assert
    with pytest.raises(ThemesEndpointError):
        resolve_endpoint()


def test_a_base_url_without_a_model_is_refused(set_setting: SettingPatcher) -> None:
    # Arrange
    _configure(set_setting, "https://models.example.net", model="  ")

    # Act / Assert
    with pytest.raises(ThemesEndpointError, match="THEMES_LLM_MODEL"):
        resolve_endpoint()


def test_the_error_never_contains_the_key_or_credentials(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting, "https://user:hunter2@models.example.net")
    set_setting("OPENAI_API_KEY", "sk-secret-value")

    # Act
    with pytest.raises(ThemesEndpointError) as caught:
        resolve_endpoint()

    # Assert
    assert "hunter2" not in str(caught.value)
    assert "sk-secret-value" not in str(caught.value)


def test_the_openai_key_is_not_sent_unless_the_operator_says_so(
    set_setting: SettingPatcher,
) -> None:
    # Arrange — OPENAI_API_KEY is normally a real OpenAI credential
    _configure(set_setting, "https://models.example.net")
    set_setting("OPENAI_API_KEY", "sk-real-openai-key")

    # Act
    endpoint = resolve_endpoint()

    # Assert
    assert endpoint is not None
    assert endpoint.api_key == ""


def test_the_openai_key_is_used_when_the_operator_opts_in(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting, "https://models.example.net")
    set_setting("OPENAI_API_KEY", "sk-from-openai-var")
    set_setting("THEMES_LLM_USE_OPENAI_API_KEY", True)

    # Act
    endpoint = resolve_endpoint()

    # Assert
    assert endpoint is not None
    assert endpoint.api_key == "sk-from-openai-var"


def test_a_themes_specific_key_wins_over_the_openai_key(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting, "https://models.example.net")
    set_setting("OPENAI_API_KEY", "sk-from-openai-var")
    set_setting("THEMES_LLM_USE_OPENAI_API_KEY", True)
    set_setting("THEMES_LLM_API_KEY", "sk-themes-only")

    # Act
    endpoint = resolve_endpoint()

    # Assert
    assert endpoint is not None
    assert endpoint.api_key == "sk-themes-only"


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("172.16.0.1", True),
        ("172.32.0.1", False),
        ("192.168.0.1", True),
        ("203.0.113.9", False),
        ("118.67.212.45", False),
        ("models.example.net", False),
    ],
)
def test_is_local_host_matches_only_this_machine_and_private_networks(
    host: str, local: bool
) -> None:
    # Act / Assert
    assert is_local_host(host) is local


# ── The client ───────────────────────────────────────────────────────────

ENDPOINT = ThemesEndpoint(
    base_url="https://models.example.net",
    model="qwen3.5-9b",
    api_key="sk-test-key",
    timeout=30,
    host="models.example.net",
)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_the_client_posts_an_openai_style_chat_request() -> None:
    # Arrange
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "hello"}}]}
        )

    # Act
    reply = await chat_complete(ENDPOINT, "the prompt", transport=_transport(handler))

    # Assert
    assert reply == "hello"
    assert seen["url"] == "https://models.example.net/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test-key"
    body = seen["body"]
    assert body["model"] == "qwen3.5-9b"  # type: ignore[index]
    assert body["messages"] == [{"role": "user", "content": "the prompt"}]  # type: ignore[index]
    assert body["temperature"] == 0  # type: ignore[index]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}  # type: ignore[index]


@pytest.mark.asyncio
async def test_the_client_sends_no_authorization_header_without_a_key() -> None:
    # Arrange
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_auth"] = "authorization" in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    keyless = ThemesEndpoint("http://localhost:8000", "m", "", 30, "localhost")

    # Act
    await chat_complete(keyless, "p", transport=_transport(handler))

    # Assert
    assert seen["has_auth"] is False


@pytest.mark.asyncio
async def test_a_reasoning_block_is_stripped_from_the_reply() -> None:
    # Arrange — Qwen-style models may emit <think>…</think> before the answer, and
    # its braces would confuse the JSON extraction downstream
    content = '<think>maybe {"a": 1} first</think>\n{"themes": []}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    # Act
    reply = await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert reply == '{"themes": []}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(401, json={"error": "bad key"}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
    ids=["500", "401", "no-choices", "wrong-shape", "not-json", "null-content"],
)
async def test_any_failure_returns_an_empty_reply_and_logs_no_secret(
    response: httpx.Response, caplog
) -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    # Act
    with caplog.at_level("WARNING"):
        reply = await chat_complete(
            ENDPOINT, "the SECRET prompt", transport=_transport(handler)
        )

    # Assert
    assert reply == ""
    assert "sk-test-key" not in caplog.text
    assert "SECRET prompt" not in caplog.text


@pytest.mark.asyncio
async def test_a_connection_error_returns_an_empty_reply(caplog) -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    # Act
    with caplog.at_level("WARNING"):
        reply = await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert reply == ""
    assert "ConnectError" in caplog.text


def test_the_api_key_is_not_in_the_endpoints_repr(set_setting: SettingPatcher) -> None:
    # Arrange — a repr ends up in tracebacks, logs and error-tracker events
    _configure(set_setting, "https://models.example.net")
    set_setting("THEMES_LLM_API_KEY", "sk-must-not-appear")

    # Act
    endpoint = resolve_endpoint()

    # Assert
    assert endpoint is not None
    assert "sk-must-not-appear" not in repr(endpoint)
    assert "sk-must-not-appear" not in str(endpoint)


@pytest.mark.parametrize(
    "url",
    [
        "http://[fe80::1",
        "http://10.0.0.1:8000:9000",
        "http://10.0.0.1:99999999",
        "https://models.example.net?x=1",
        "https://models.example.net#frag",
        "https://models.example.net/v1?key=abc",
    ],
)
def test_an_address_that_does_not_parse_or_carries_a_query_is_refused_not_crashed(
    url: str, set_setting: SettingPatcher
) -> None:
    # Arrange
    _configure(set_setting, url)

    # Act / Assert
    with pytest.raises(ThemesEndpointError):
        resolve_endpoint()


@pytest.mark.parametrize(
    "url",
    [
        "https://user:s3cret@10.0.0.1＃x",
        "http://[fe80::1",
        "ftp://models.example.net",
        "not a url",
    ],
)
def test_no_refusal_message_quotes_the_address(
    url: str, set_setting: SettingPatcher
) -> None:
    # Arrange — a parser's own error text can quote the credentials inside a URL
    _configure(set_setting, url)

    # Act
    with pytest.raises(ThemesEndpointError) as caught:
        resolve_endpoint()

    # Assert
    message = str(caught.value)
    assert "s3cret" not in message
    assert "fe80" not in message
    assert "models.example.net" not in message


@pytest.mark.parametrize("suffix", ["/v1", "/V1", "/v1/"])
def test_the_v1_suffix_is_stripped_in_any_case(
    suffix: str, set_setting: SettingPatcher
) -> None:
    # Arrange
    _configure(set_setting, f"https://models.example.net{suffix}")

    # Act
    endpoint = resolve_endpoint()

    # Assert
    assert endpoint is not None
    assert endpoint.base_url == "https://models.example.net"


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("::1", True),
        ("fe80::1", True),
        ("fd00::5", True),
        ("169.254.1.1", True),
        ("2001:db8::1", False),
    ],
)
def test_is_local_host_covers_ipv6_and_link_local_ranges(
    host: str, local: bool
) -> None:
    # Act / Assert
    assert is_local_host(host) is local


# ── The server is not trusted (11.18 review) ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [42, ["a", "b"], {"k": "v"}, True],
    ids=["int", "list", "dict", "bool"],
)
async def test_a_reply_that_is_not_text_is_an_empty_reply_not_an_exception(
    content: object,
) -> None:
    # Arrange — an exception here would carry the key in its stack frames
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    # Act
    reply = await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert reply == ""


@pytest.mark.asyncio
async def test_a_recursion_error_while_decoding_is_an_empty_reply() -> None:
    # Arrange — how deep is too deep varies by build, so force the error
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    # Act
    with patch("app.services.openai_compatible.json.loads", side_effect=RecursionError):
        reply = await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert reply == ""


@pytest.mark.asyncio
async def test_a_body_over_the_size_cap_is_refused_without_being_buffered() -> None:
    # Arrange
    huge = "x" * (MAX_RESPONSE_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": huge}}]})

    # Act
    reply = await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert reply == ""


@pytest.mark.asyncio
async def test_a_body_just_under_the_cap_is_accepted() -> None:
    # Arrange — leave room for the JSON envelope
    content = "y" * (MAX_RESPONSE_BYTES - 200)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    # Act
    reply = await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert reply == content


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("<think>a</think>answer", "answer"),
        ("<think>a</think>x<think>b</think>final", "final"),
        ("plain answer", "plain answer"),
        ('<think>never closed {"themes": []}', ""),
        ("  spaced  ", "spaced"),
    ],
)
def test_strip_reasoning_keeps_only_the_answer(content: str, expected: str) -> None:
    # Act / Assert
    assert strip_reasoning(content) == expected


def test_stripping_an_unclosed_reasoning_block_is_linear_time() -> None:
    # Arrange — a lazy regex over this is quadratic and stalls the event loop
    hostile = "<think>" * 30_000

    # Act
    started = time.perf_counter()
    result = strip_reasoning(hostile)
    elapsed = time.perf_counter() - started

    # Assert
    assert result == ""
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_the_whole_call_has_one_deadline() -> None:
    # Arrange — a server that never answers
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    slow = ThemesEndpoint(
        "https://models.example.net", "m", "k", 1, "models.example.net"
    )

    # Act
    started = time.perf_counter()
    reply = await chat_complete(slow, "p", transport=_transport(handler))
    elapsed = time.perf_counter() - started

    # Assert
    assert reply == ""
    assert elapsed < 3


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed_and_yields_an_empty_reply() -> None:
    # Arrange — following it could carry the Authorization header to another host
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "models.example.net":
            return httpx.Response(
                307, headers={"Location": "https://evil.example.org/x"}
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "leaked"}}]}
        )

    # Act
    reply = await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert reply == ""
    assert seen == ["https://models.example.net/v1/chat/completions"]


@pytest.mark.asyncio
async def test_a_failed_status_is_an_empty_reply() -> None:
    # Arrange — a 200 with a good-looking body must not be needed to fail safe;
    # a 500 with a valid-looking body must not be accepted either
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"choices": [{"message": {"content": "answer"}}]}
        )

    # Act
    reply = await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert reply == ""


@pytest.mark.asyncio
async def test_proxy_environment_variables_are_not_honoured() -> None:
    # Arrange
    captured: dict[str, object] = {}
    real_client = httpx.AsyncClient

    def spy(*args: object, **kwargs: object) -> httpx.AsyncClient:
        captured.update(kwargs)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    # Act
    with patch("app.services.openai_compatible.httpx.AsyncClient", side_effect=spy):
        await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert — a proxy would otherwise see the key and the summaries, even for localhost
    assert captured["trust_env"] is False


@pytest.mark.asyncio
async def test_the_reply_length_is_bounded_in_the_request() -> None:
    # Arrange
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    # Act
    await chat_complete(ENDPOINT, "p", transport=_transport(handler))

    # Assert
    assert seen["max_tokens"] == MAX_REPLY_TOKENS


@pytest.mark.asyncio
async def test_an_invalid_request_url_is_an_empty_reply(caplog) -> None:
    # Arrange — httpx.InvalidURL is not an httpx.HTTPError
    broken = ThemesEndpoint("http://10.0.0.1:99999999", "m", "sk-key", 5, "10.0.0.1")

    # Act
    with caplog.at_level("WARNING"):
        reply = await chat_complete(broken, "the prompt")

    # Assert
    assert reply == ""
    assert "sk-key" not in caplog.text
