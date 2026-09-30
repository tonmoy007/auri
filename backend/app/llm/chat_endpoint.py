"""Where priest mode sends its questions, and whether that address may be used.

Priest text (a person's question and passages from the study vault) only ever goes to
a server the operator runs: vLLM as the primary, local Ollama as the fallback. A
hosted provider is refused outright, whatever the configuration says, because this
text must not reach a third party.

The primary is ``PRIEST_LLM_BASE_URL``. While that is unset the prototype reuses the
themes endpoint exactly as ``themes_endpoint`` resolves it (same validation, same
opt-ins, same key rule) with the priest model, so a box that already serves themes
can serve the Guide without new configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

from app.config import settings
from app.exceptions import PriestEndpointError, ThemesEndpointError
from app.priest import priest_config
from app.services import themes_endpoint

EndpointKind = Literal["vllm", "ollama"]

# Hosted model APIs; priest text must never be sent to any of them.
THIRD_PARTY_HOSTS: Final = frozenset(
    {"api.openai.com", "generativelanguage.googleapis.com", "api.anthropic.com"}
)
_THEMES_PREFIX: Final = "THEMES_LLM_"
_PRIEST_PREFIX: Final = "PRIEST_LLM_"


@dataclass(frozen=True)
class ChatEndpoint:
    """A validated OpenAI-compatible chat server.

    ``base_url`` has no trailing slash and no ``/v1``; the client adds the path.
    """

    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: int
    host: str
    kind: EndpointKind


def is_third_party_host(host: str) -> bool:
    """Whether *host* is a hosted model provider (or a subdomain of one)."""
    name = host.strip().lower().rstrip(".")
    return any(
        name == banned or name.endswith(f".{banned}") for banned in THIRD_PARTY_HOSTS
    )


def refuse_third_party_host(host: str) -> None:
    """Raise unless *host* is acceptable for priest text.

    Raises:
        PriestEndpointError: For a hosted provider. The message never quotes the address.
    """
    if is_third_party_host(host):
        raise PriestEndpointError(
            "the chat server is a third-party model provider; priest text must "
            "stay on servers the operator runs"
        )


def _checked_base(raw: str, name: str) -> tuple[str, str]:
    """Return ``(base_url, host)`` for *raw*, or raise ``PriestEndpointError``.

    Validation is ``themes_endpoint``'s (http(s) only, no credentials, no query or
    fragment, fixed messages); its setting name is swapped for *name* so the operator
    is pointed at the right key.
    """
    try:
        base, host, scheme = themes_endpoint._validated_base(raw)
    except ThemesEndpointError as exc:
        message = str(exc).replace(_THEMES_PREFIX, _PRIEST_PREFIX)
        raise PriestEndpointError(message) from None
    refuse_third_party_host(host)
    if (
        scheme == "http"
        and not themes_endpoint.is_local_host(host)
        and not settings.PRIEST_LLM_ALLOW_INSECURE_HTTP
    ):
        raise PriestEndpointError(
            f"plain http to a public address would send questions and the key "
            f"unencrypted; use https, a tunnel, or a private address ({name})"
        )
    return base, host.lower()


def _from_themes() -> ChatEndpoint | None:
    """The prototype primary: the themes endpoint, asked for the priest model."""
    try:
        themes = themes_endpoint.resolve_endpoint()
    except ThemesEndpointError as exc:
        raise PriestEndpointError(str(exc)) from None
    if themes is None:
        return None
    refuse_third_party_host(themes.host)
    return ChatEndpoint(
        base_url=themes.base_url,
        model=priest_config.llm_model(),
        api_key=themes.api_key,
        timeout_seconds=priest_config.llm_timeout_seconds(),
        host=themes.host.lower(),
        kind="vllm",
    )


def resolve_primary() -> ChatEndpoint | None:
    """Return the primary chat server, or ``None`` when none is configured.

    Returns:
        The endpoint from ``PRIEST_LLM_BASE_URL`` (key: ``PRIEST_LLM_API_KEY`` only),
        else the themes endpoint with the priest model, else ``None``.

    Raises:
        PriestEndpointError: If the address is malformed, a hosted provider, or plain
            http to a public host without ``PRIEST_LLM_ALLOW_INSECURE_HTTP``.
    """
    raw = settings.PRIEST_LLM_BASE_URL.strip()
    if not raw:
        return _from_themes()
    base, host = _checked_base(raw, "PRIEST_LLM_BASE_URL")
    return ChatEndpoint(
        base_url=base,
        model=priest_config.llm_model(),
        api_key=settings.PRIEST_LLM_API_KEY,
        timeout_seconds=priest_config.llm_timeout_seconds(),
        host=host,
        kind="vllm",
    )


def resolve_fallback() -> ChatEndpoint | None:
    """Return the fallback (Ollama) server, or ``None`` when no model is named.

    The fallback is unauthenticated: it never carries an API key.

    Raises:
        PriestEndpointError: Under the same rules as ``resolve_primary``.
    """
    model = priest_config.fallback_model()
    if not model:
        return None
    base, host = _checked_base(
        priest_config.fallback_base_url(), "PRIEST_FALLBACK_BASE_URL"
    )
    return ChatEndpoint(
        base_url=base,
        model=model,
        api_key="",
        timeout_seconds=priest_config.llm_timeout_seconds(),
        host=host,
        kind="ollama",
    )
