"""Tests for the priest-mode chat transport: endpoint rules, client and chain (13.13).

Priest text (a question, retrieved notes) goes to the chat server, so the address
rules are tested at their edges, and the client is driven through an in-process
transport (``httpx.MockTransport``): no test touches the network, Ollama or vLLM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

import httpx
import pytest
from app.exceptions import PriestEndpointError, PriestLLMError
from app.llm.chat_client import (
    MAX_RESPONSE_BYTES,
    ChatClient,
    ChatFailure,
    ChatMessage,
    PriestLLMChain,
)
from app.llm.chat_endpoint import ChatEndpoint, resolve_fallback, resolve_primary

from tests.conftest import SettingPatcher

SECRET_KEY = "sk-very-secret-key-123"
SECRET_PROMPT = "my private question about my manager"
MESSAGES = [
    ChatMessage(role="system", content="You are a guide."),
    ChatMessage(role="user", content=SECRET_PROMPT),
]
GOOD_BODY = {"choices": [{"message": {"role": "assistant", "content": "hello there"}}]}
THIRD_PARTY_HOSTS = (
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "api.anthropic.com",
)
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def blank_endpoint_settings(set_setting: SettingPatcher) -> None:
    """Start every test with no endpoint, no keys and no opt-ins."""
    for name, value in (
        ("PRIEST_LLM_BASE_URL", ""),
        ("PRIEST_LLM_API_KEY", ""),
        ("PRIEST_LLM_MODEL", "qwen3.5-9b"),
        ("PRIEST_LLM_ALLOW_INSECURE_HTTP", False),
        ("PRIEST_FALLBACK_BASE_URL", ""),
        ("PRIEST_FALLBACK_MODEL", ""),
        ("OLLAMA_BASE_URL", "http://localhost:11434"),
        ("OPENAI_API_KEY", ""),
    ):
        set_setting(name, value)


def _endpoint(
    kind: str = "vllm", key: str = "", host: str = "llm.test", timeout: int = 5
) -> ChatEndpoint:
    return ChatEndpoint(
        base_url=f"http://{host}:8000",
        model="qwen3.5-9b",
        api_key=key,
        timeout_seconds=timeout,
        host=host,
        kind=kind,  # type: ignore[arg-type]
    )


def _client(
    handler: Handler, endpoint: ChatEndpoint | None = None
) -> tuple[ChatClient, list[httpx.Request]]:
    """A ChatClient on a mock transport, plus the list of requests it received."""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    return ChatClient(endpoint or _endpoint(), client=http), seen


def _reply(text: str = "hello there") -> Handler:
    body = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    return lambda request: httpx.Response(200, json=body)


def _always(response: httpx.Response) -> Handler:
    return lambda request: response


def _raising(error: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


async def _ask(client: ChatClient | PriestLLMChain, **kwargs: object) -> str:
    result = await client.complete(MESSAGES, request_id="req-1", **kwargs)  # type: ignore[arg-type]
    return result.text


# ── endpoint resolution ──────────────────────────────────────────────────


def test_nothing_configured_resolves_to_no_primary_and_no_fallback() -> None:
    # Act / Assert
    assert resolve_primary() is None
    assert resolve_fallback() is None


@pytest.mark.parametrize(
    "url",
    [
        "https://models.example.net",
        "https://models.example.net/",
        "https://models.example.net/v1",
        "http://localhost:8000/v1/",
    ],
)
def test_primary_base_url_loses_its_v1_and_trailing_slash(
    url: str, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", url)
    set_setting("PRIEST_LLM_API_KEY", SECRET_KEY)

    # Act
    endpoint = resolve_primary()

    # Assert
    assert endpoint is not None
    assert not endpoint.base_url.endswith(("/", "/v1"))
    assert endpoint.model == "qwen3.5-9b"
    assert endpoint.kind == "vllm"
    assert endpoint.api_key == SECRET_KEY
    assert endpoint.timeout_seconds == 20


def test_the_api_key_never_appears_in_the_endpoint_repr(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "http://localhost:8000")
    set_setting("PRIEST_LLM_API_KEY", SECRET_KEY)

    # Act
    text = repr(resolve_primary())

    # Assert
    assert SECRET_KEY not in text


def test_primary_takes_only_the_priest_key_never_openai_or_themes(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "http://localhost:8000")
    set_setting("OPENAI_API_KEY", "openai-key")
    set_setting("THEMES_LLM_API_KEY", "themes-key")

    # Act
    endpoint = resolve_primary()

    # Assert
    assert endpoint is not None
    assert endpoint.api_key == ""


@pytest.mark.parametrize(
    "url",
    [
        "ftp://llm.internal.example",
        "not a url",
        "https://user:hunter2@llm.internal.example",
        "https://llm.internal.example/v1?key=hunter2",
        "https://llm.internal.example/#hunter2",
        "http://203.0.113.9:8000",
    ],
)
def test_unusable_primary_addresses_are_refused_without_quoting_them(
    url: str, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", url)

    # Act
    with pytest.raises(PriestEndpointError) as excinfo:
        resolve_primary()

    # Assert
    message = str(excinfo.value)
    assert "hunter2" not in message
    assert "203.0.113.9" not in message
    assert "llm.internal.example" not in message
    assert "THEMES" not in message


def test_plain_http_to_a_public_host_is_allowed_only_with_the_opt_in(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "http://203.0.113.9:8000")
    set_setting("PRIEST_LLM_ALLOW_INSECURE_HTTP", True)

    # Act
    endpoint = resolve_primary()

    # Assert
    assert endpoint is not None
    assert endpoint.host == "203.0.113.9"


def test_with_no_priest_url_the_themes_endpoint_is_reused_for_the_prototype(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("THEMES_LLM_BASE_URL", "http://10.0.0.5:8000/v1")
    set_setting("THEMES_LLM_MODEL", "some-themes-model")
    set_setting("THEMES_LLM_API_KEY", "themes-key")

    # Act
    endpoint = resolve_primary()

    # Assert
    assert endpoint is not None
    assert endpoint.base_url == "http://10.0.0.5:8000"
    assert endpoint.model == "qwen3.5-9b"  # the priest model, not the themes one
    assert endpoint.api_key == "themes-key"
    assert endpoint.timeout_seconds == 20  # the priest limit, not the themes one


def test_a_priest_url_wins_over_the_themes_endpoint(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "http://10.0.0.7:8000")
    set_setting("THEMES_LLM_BASE_URL", "http://10.0.0.5:8000")
    set_setting("THEMES_LLM_MODEL", "m")

    # Act
    endpoint = resolve_primary()

    # Assert
    assert endpoint is not None
    assert endpoint.host == "10.0.0.7"


def test_a_bad_themes_endpoint_is_refused_with_a_safe_message(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("THEMES_LLM_BASE_URL", "https://user:hunter2@llm.internal.example")
    set_setting("THEMES_LLM_MODEL", "m")

    # Act
    with pytest.raises(PriestEndpointError) as excinfo:
        resolve_primary()

    # Assert
    assert "hunter2" not in str(excinfo.value)
    assert "llm.internal.example" not in str(excinfo.value)


@pytest.mark.parametrize(
    "host", [*THIRD_PARTY_HOSTS, "API.OpenAI.com", "api.anthropic.com."]
)
def test_no_resolver_ever_returns_a_third_party_host(
    host: str, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", f"https://{host}/v1")

    # Act / Assert — explicit primary
    with pytest.raises(PriestEndpointError):
        resolve_primary()

    # Act / Assert — prototype fallback through the themes endpoint
    set_setting("PRIEST_LLM_BASE_URL", "")
    set_setting("THEMES_LLM_BASE_URL", f"https://{host}")
    set_setting("THEMES_LLM_MODEL", "m")
    with pytest.raises(PriestEndpointError):
        resolve_primary()

    # Act / Assert — the local fallback
    set_setting("PRIEST_FALLBACK_BASE_URL", f"https://{host}/v1")
    set_setting("PRIEST_FALLBACK_MODEL", "llama3.2:3b")
    with pytest.raises(PriestEndpointError):
        resolve_fallback()


def test_the_denylist_also_covers_a_subdomain_of_a_provider(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "https://eu.api.openai.com")

    # Act / Assert
    with pytest.raises(PriestEndpointError):
        resolve_primary()


@pytest.mark.parametrize(
    "address",
    [
        "https://api\u3002openai\u3002com/v1",  # ideographic full stops
        "https://openrouter.ai/api/v1",
        "https://myorg.openai.azure.com",
        "https://us-central1-aiplatform.googleapis.com",
        "https://api.groq.com/openai/v1",
        "https://api.together.xyz/v1",
        "https://gateway.ai.cloudflare.com/v1/acct/gw/openai",
    ],
)
def test_other_hosted_model_hosts_and_look_alike_spellings_are_refused(
    address: str, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", address)

    # Act / Assert
    with pytest.raises(PriestEndpointError):
        resolve_primary()


def test_a_cloud_hosted_ollama_model_is_refused_for_the_fallback(
    set_setting: SettingPatcher,
) -> None:
    # Arrange — Ollama runs a "-cloud" model on a third party's servers
    set_setting("PRIEST_FALLBACK_MODEL", "gpt-oss:120b-cloud")

    # Act / Assert
    with pytest.raises(PriestEndpointError):
        resolve_fallback()


def test_a_cloud_hosted_model_is_refused_for_the_primary(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "http://localhost:8000")
    set_setting("PRIEST_LLM_MODEL", "some-model-cloud")

    # Act / Assert
    with pytest.raises(PriestEndpointError):
        resolve_primary()


def test_the_fallback_is_off_without_a_model(set_setting: SettingPatcher) -> None:
    # Arrange
    set_setting("PRIEST_FALLBACK_BASE_URL", "http://localhost:11434/v1")

    # Act / Assert
    assert resolve_fallback() is None


def test_the_fallback_defaults_to_ollama_v1_and_uses_no_key(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_FALLBACK_MODEL", "llama3.2:3b")
    set_setting("PRIEST_LLM_API_KEY", SECRET_KEY)

    # Act
    endpoint = resolve_fallback()

    # Assert
    assert endpoint is not None
    assert endpoint.base_url == "http://localhost:11434"
    assert endpoint.model == "llama3.2:3b"
    assert endpoint.kind == "ollama"
    assert endpoint.api_key == ""


def test_the_fallback_follows_an_explicit_base_url(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_FALLBACK_MODEL", "llama3.2:3b")
    set_setting("PRIEST_FALLBACK_BASE_URL", "http://192.168.1.4:11434/v1/")

    # Act
    endpoint = resolve_fallback()

    # Assert
    assert endpoint is not None
    assert endpoint.base_url == "http://192.168.1.4:11434"


# ── request shape ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_request_has_the_expected_url_headers_and_body() -> None:
    # Arrange
    client, seen = _client(_reply(), _endpoint(key=SECRET_KEY))

    # Act
    text = await _ask(client, max_tokens=300, temperature=0.1, seed=7)

    # Assert
    assert text == "hello there"
    request = seen[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert str(request.url) == "http://llm.test:8000/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET_KEY}"
    assert request.headers["x-request-id"] == "req-1"
    assert body["model"] == "qwen3.5-9b"
    assert body["messages"] == [
        {"role": "system", "content": "You are a guide."},
        {"role": "user", "content": SECRET_PROMPT},
    ]
    assert body["max_tokens"] == 300
    assert body["temperature"] == 0.1
    assert body["seed"] == 7
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" not in body


@pytest.mark.asyncio
async def test_no_authorization_header_and_no_seed_when_there_is_none() -> None:
    # Arrange
    client, seen = _client(_reply(), _endpoint(key=""))

    # Act
    await _ask(client)

    # Assert
    body = json.loads(seen[0].content)
    assert "authorization" not in seen[0].headers
    assert "seed" not in body
    assert body["max_tokens"] == 600
    assert body["temperature"] == 0.2


@pytest.mark.asyncio
async def test_vllm_gets_a_json_schema_response_format() -> None:
    # Arrange
    schema = {"type": "object", "properties": {"kind": {"type": "string"}}}
    client, seen = _client(_reply(), _endpoint(kind="vllm"))

    # Act
    await _ask(client, json_schema=schema)

    # Assert
    response_format = json.loads(seen[0].content)["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == schema


@pytest.mark.asyncio
async def test_ollama_gets_a_json_object_response_format_instead() -> None:
    # Arrange
    client, seen = _client(_reply(), _endpoint(kind="ollama"))

    # Act
    await _ask(client, json_schema={"type": "object"})

    # Assert
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_think_blocks_are_stripped_from_the_reply() -> None:
    # Arrange
    client, _ = _client(_reply('<think>plan {"x": 1}</think>\n{"kind": "answer"}'))

    # Act
    text = await _ask(client)

    # Assert
    assert text == '{"kind": "answer"}'


@pytest.mark.asyncio
async def test_a_reply_that_is_only_an_unclosed_think_block_is_empty() -> None:
    # Arrange
    client, _ = _client(_reply("<think>never finished"))

    # Act
    with pytest.raises(ChatFailure) as excinfo:
        await _ask(client)

    # Assert
    assert excinfo.value.reason == "empty"


# ── failure handling ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_timeout_is_not_retried() -> None:
    # Arrange
    client, seen = _client(_raising(httpx.ReadTimeout("slow")))

    # Act
    with pytest.raises(ChatFailure) as excinfo:
        await _ask(client)

    # Assert
    assert len(seen) == 1
    assert excinfo.value.reason == "timeout"


@pytest.mark.asyncio
async def test_a_connect_timeout_is_not_retried_either() -> None:
    # Arrange
    client, seen = _client(_raising(httpx.ConnectTimeout("slow")))

    # Act
    with pytest.raises(ChatFailure):
        await _ask(client)

    # Assert
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_connect_error_is_retried_once_then_succeeds() -> None:
    # Arrange
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=GOOD_BODY)

    client, _ = _client(handler)

    # Act
    text = await _ask(client)

    # Assert
    assert text == "hello there"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_two_connect_errors_fail_after_exactly_two_attempts() -> None:
    # Arrange
    client, seen = _client(_raising(httpx.ConnectError("refused")))

    # Act
    with pytest.raises(ChatFailure) as excinfo:
        await _ask(client)

    # Assert
    assert len(seen) == 2
    assert excinfo.value.reason == "connect"


@pytest.mark.asyncio
async def test_a_5xx_is_not_retried() -> None:
    # Arrange
    client, seen = _client(lambda request: httpx.Response(503, text="overloaded"))

    # Act
    with pytest.raises(ChatFailure) as excinfo:
        await _ask(client)

    # Assert
    assert len(seen) == 1
    assert excinfo.value.status == 503


@pytest.mark.asyncio
async def test_one_overall_deadline_bounds_the_whole_call() -> None:
    # Arrange
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json=GOOD_BODY)

    client, _ = _client(slow)  # type: ignore[arg-type]

    # Act
    started = asyncio.get_running_loop().time()
    with pytest.raises(ChatFailure) as excinfo:
        await _ask(client, timeout=0.05)

    # Assert
    assert asyncio.get_running_loop().time() - started < 1
    assert excinfo.value.reason == "timeout"


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://evil.test/steal"})

    client, seen = _client(handler)

    # Act
    with pytest.raises(ChatFailure):
        await _ask(client)

    # Assert
    assert [r.url.host for r in seen] == ["llm.test"]


@pytest.mark.asyncio
async def test_a_reply_over_the_size_cap_is_refused() -> None:
    # Arrange
    big = "x" * (MAX_RESPONSE_BYTES + 10)
    client, _ = _client(_reply(big))

    # Act
    with pytest.raises(ChatFailure) as excinfo:
        await _ask(client)

    # Assert
    assert excinfo.value.reason == "too_large"
    assert MAX_RESPONSE_BYTES == 256 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    ["not json", "{}", '{"choices": []}', '{"choices": [{"message": {"content": 7}}]}'],
)
async def test_a_malformed_body_is_a_failure_not_a_crash(body: str) -> None:
    # Arrange
    client, _ = _client(lambda request: httpx.Response(200, text=body))

    # Act / Assert
    with pytest.raises(PriestLLMError):
        await _ask(client)


@pytest.mark.asyncio
async def test_errors_and_logs_never_contain_the_key_the_prompt_or_the_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    leaked_reply = "THE-MODEL-REPLY-TEXT"
    cases = [
        httpx.Response(500, text=leaked_reply),
        httpx.Response(400, text=leaked_reply),
        httpx.Response(200, text=f"garbage {leaked_reply}"),
    ]
    caplog.set_level(logging.DEBUG)

    for response in cases:
        client, _ = _client(_always(response), _endpoint(key=SECRET_KEY))

        # Act
        with pytest.raises(PriestLLMError) as excinfo:
            await _ask(client)

        # Assert
        shown = f"{excinfo.value!s} {excinfo.value!r}"
        for secret in (SECRET_KEY, SECRET_PROMPT, leaked_reply):
            assert secret not in shown
        assert excinfo.value.__cause__ is None
    assert all(
        secret not in caplog.text
        for secret in (SECRET_KEY, SECRET_PROMPT, leaked_reply)
    )


@pytest.mark.parametrize("host", THIRD_PARTY_HOSTS)
def test_a_client_for_a_third_party_host_cannot_be_built(host: str) -> None:
    # Arrange
    endpoint = _endpoint(host=host)

    # Act / Assert — refused at construction, so nothing can ever be sent
    with pytest.raises(PriestEndpointError):
        ChatClient(
            endpoint, client=httpx.AsyncClient(transport=httpx.MockTransport(_reply()))
        )


# ── the primary-then-fallback chain ──────────────────────────────────────


def _chain(
    primary: Handler | None, fallback: Handler | None
) -> tuple[PriestLLMChain, list[httpx.Request], list[httpx.Request]]:
    first, first_seen = (
        _client(primary, _endpoint("vllm", host="vllm.test")) if primary else (None, [])
    )
    second, second_seen = (
        _client(fallback, _endpoint("ollama", host="ollama.test"))
        if fallback
        else (None, [])
    )
    return PriestLLMChain(first, second), first_seen, second_seen


@pytest.mark.asyncio
async def test_a_healthy_primary_answers_and_the_fallback_is_never_called() -> None:
    # Arrange
    chain, _, fallback_seen = _chain(_reply("from vllm"), _reply("from ollama"))

    # Act
    result = await chain.complete(MESSAGES, request_id="r")

    # Assert
    assert result.text == "from vllm"
    assert result.endpoint_kind == "vllm"
    assert fallback_seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "primary",
    [
        lambda request: httpx.Response(500, text="boom"),
        lambda request: httpx.Response(503, text="boom"),
        lambda request: httpx.Response(408, text="slow"),
        lambda request: httpx.Response(429, text="busy"),
        _raising(httpx.ConnectError("refused")),
        _raising(httpx.ReadTimeout("slow")),
        _reply("<think>x</think>"),
        lambda request: httpx.Response(200, text="not json"),
    ],
)
async def test_the_fallback_answers_when_the_primary_fails_that_way(
    primary: Handler,
) -> None:
    # Arrange
    chain, _, _ = _chain(primary, _reply("from ollama"))

    # Act
    result = await chain.complete(MESSAGES, request_id="r")

    # Assert
    assert result.text == "from ollama"
    assert result.endpoint_kind == "ollama"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_a_client_error_is_not_sent_to_the_fallback(status: int) -> None:
    # Arrange
    chain, _, fallback_seen = _chain(
        lambda request: httpx.Response(status, text="bad"), _reply("from ollama")
    )

    # Act
    with pytest.raises(PriestLLMError):
        await chain.complete(MESSAGES, request_id="r")

    # Assert
    assert fallback_seen == []


@pytest.mark.asyncio
async def test_with_no_primary_the_fallback_answers() -> None:
    # Arrange
    chain, _, _ = _chain(None, _reply("from ollama"))

    # Act
    result = await chain.complete(MESSAGES, request_id="r")

    # Assert
    assert result.endpoint_kind == "ollama"


@pytest.mark.asyncio
async def test_with_nothing_configured_the_chain_raises() -> None:
    # Arrange
    chain = PriestLLMChain(None, None)

    # Act / Assert
    with pytest.raises(PriestLLMError):
        await chain.complete(MESSAGES, request_id="r")


@pytest.mark.asyncio
async def test_when_both_servers_fail_the_chain_raises_a_safe_error() -> None:
    # Arrange
    chain, _, _ = _chain(
        lambda request: httpx.Response(500, text="THE-REPLY"),
        _raising(httpx.ConnectError("refused")),
    )

    # Act
    with pytest.raises(PriestLLMError) as excinfo:
        await chain.complete(MESSAGES, request_id="r")

    # Assert
    assert SECRET_PROMPT not in str(excinfo.value)
    assert "THE-REPLY" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_only_the_configured_servers_are_ever_contacted() -> None:
    # Arrange
    chain, first_seen, second_seen = _chain(
        lambda request: httpx.Response(500, text="boom"), _reply("ok")
    )

    # Act
    await chain.complete(MESSAGES, request_id="r")

    # Assert — never a third party, never anything but the two hosts
    hosts = {r.url.host for r in [*first_seen, *second_seen]}
    assert hosts == {"vllm.test", "ollama.test"}
    assert not hosts & set(THIRD_PARTY_HOSTS)


def test_the_themes_shortcut_uses_the_themes_model_when_no_priest_model_is_named(
    set_setting: SettingPatcher,
) -> None:
    # Arrange — the prototype reuses the themes server, which already names its model
    set_setting("THEMES_LLM_BASE_URL", "http://10.0.0.5:8000/v1")
    set_setting("THEMES_LLM_MODEL", "RedHatAI/Qwen3.5-9B-FP8-dynamic")
    set_setting("PRIEST_LLM_MODEL", "")

    # Act
    endpoint = resolve_primary()

    # Assert
    assert endpoint is not None
    assert endpoint.model == "RedHatAI/Qwen3.5-9B-FP8-dynamic"


def test_a_named_priest_model_wins_over_the_themes_model(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("THEMES_LLM_BASE_URL", "http://10.0.0.5:8000/v1")
    set_setting("THEMES_LLM_MODEL", "themes-model")
    set_setting("PRIEST_LLM_MODEL", "priest-model")

    # Act
    endpoint = resolve_primary()

    # Assert
    assert endpoint is not None and endpoint.model == "priest-model"


def test_its_own_server_needs_its_own_model_name(set_setting: SettingPatcher) -> None:
    # Arrange — guessing a model name would send every request to a server that has none
    set_setting("PRIEST_LLM_BASE_URL", "https://llm.example.test")
    set_setting("PRIEST_LLM_MODEL", "")

    # Act / Assert
    with pytest.raises(PriestEndpointError, match="PRIEST_LLM_MODEL"):
        resolve_primary()


# ── review fixes: timeouts, decoding errors, compression ─────────────────────


@pytest.mark.asyncio
async def test_a_black_holed_server_is_given_up_on_at_a_short_connect_timeout() -> None:
    # Arrange — a dropped SYN must not use the whole deadline before the fallback runs
    client, seen = _client(_reply())

    # Act
    await _ask(client, timeout=20)

    # Assert
    timeout = seen[0].extensions["timeout"]
    assert timeout["read"] == 20
    assert timeout["connect"] <= 3.0


@pytest.mark.asyncio
async def test_the_fallback_gets_only_the_time_the_primary_left() -> None:
    # Arrange
    def slow_failure(request: httpx.Request) -> httpx.Response:
        time.sleep(0.3)
        raise httpx.ConnectTimeout("no route")

    chain, _, fallback_seen = _chain(slow_failure, _reply("from ollama"))

    # Act
    result = await chain.complete(MESSAGES, request_id="r", timeout=2.0)

    # Assert — one overall budget, not a fresh one per server
    assert result.text == "from ollama"
    assert fallback_seen[0].extensions["timeout"]["read"] < 1.8


@pytest.mark.asyncio
async def test_a_bad_content_encoding_is_a_fixed_failure_not_a_crash() -> None:
    # Arrange
    client, _ = _client(_raising(httpx.DecodingError("bad gzip")))

    # Act / Assert
    with pytest.raises(PriestLLMError):
        await _ask(client)


@pytest.mark.asyncio
async def test_compression_is_not_requested() -> None:
    # Arrange — the size cap applies to bytes read, so the reply must not expand
    client, seen = _client(_reply())

    # Act
    await _ask(client)

    # Assert
    assert seen[0].headers["Accept-Encoding"] == "identity"
