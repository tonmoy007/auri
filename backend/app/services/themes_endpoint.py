"""Where theme grouping asks its model, and whether that address may be used.

By default themes are grouped by the local Ollama model. An operator can point
them at an OpenAI-compatible server instead (vLLM, for example) with
``THEMES_LLM_BASE_URL``. That server receives de-identified summaries and an API
key, so the address is checked before anything is sent: plain http to a public
address would cross the internet unencrypted and is refused unless the
operator says otherwise on purpose.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.exceptions import ThemesEndpointError

_LOCAL_HOSTNAMES: Final = frozenset({"localhost", "host.docker.internal", "ollama"})
_LOCAL_NETWORKS: Final = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    )
)


@dataclass(frozen=True)
class ThemesEndpoint:
    """A validated OpenAI-compatible server for theme grouping."""

    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout: int
    host: str


def is_local_host(host: str) -> bool:
    """Whether *host* is this machine or an address on a private network.

    Only loopback, RFC 1918, link-local and unique-local ranges count.
    ``ipaddress``'s own ``is_private`` is wider (it includes documentation and
    reserved ranges) and would call a public-looking address private.

    Args:
        host: A hostname or IP literal.

    Returns:
        ``True`` if the host is local or private.
    """
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _LOCAL_NETWORKS)


def _validated_base(raw: str) -> tuple[str, str, str]:
    """Return ``(base_url, host, scheme)``, or raise if *raw* is unusable.

    Raises:
        ThemesEndpointError: For an address that does not parse, is not http(s),
            has no host, embeds credentials, or carries a query or fragment.
            Messages are fixed text: they never quote the address, because the
            parser's own errors can quote the credentials inside it.
    """
    try:
        parsed = urlparse(raw)
        hostname = parsed.hostname
        parsed.port  # noqa: B018 - raises ValueError for an out-of-range port
        httpx.URL(raw)
    except (ValueError, httpx.InvalidURL) as exc:
        raise ThemesEndpointError("THEMES_LLM_BASE_URL is not a valid address") from exc
    if parsed.scheme not in ("http", "https") or not hostname:
        raise ThemesEndpointError("THEMES_LLM_BASE_URL must be an http(s) address")
    if parsed.username or parsed.password:
        raise ThemesEndpointError(
            "THEMES_LLM_BASE_URL must not contain credentials; use THEMES_LLM_API_KEY"
        )
    if parsed.query or parsed.fragment:
        raise ThemesEndpointError(
            "THEMES_LLM_BASE_URL must not contain a query or fragment"
        )
    base = raw.rstrip("/")
    if base.lower().endswith("/v1"):
        base = base[: -len("/v1")]
    return base, hostname, parsed.scheme


def _api_key() -> str:
    """The key to send: the themes-specific one, or OPENAI_API_KEY only if allowed."""
    if settings.THEMES_LLM_API_KEY:
        return settings.THEMES_LLM_API_KEY
    if settings.THEMES_LLM_USE_OPENAI_API_KEY:
        return settings.OPENAI_API_KEY
    return ""


def resolve_endpoint() -> ThemesEndpoint | None:
    """Return the configured themes endpoint, or ``None`` to use local Ollama.

    Returns:
        The validated endpoint, or ``None`` when ``THEMES_LLM_BASE_URL`` is unset.

    Raises:
        ThemesEndpointError: If it is set but must not be used: malformed, no
            model named, or plain http to a public address without
            ``THEMES_LLM_ALLOW_INSECURE_HTTP``.
    """
    raw = settings.THEMES_LLM_BASE_URL.strip()
    if not raw:
        return None
    base, host, scheme = _validated_base(raw)
    model = settings.THEMES_LLM_MODEL.strip()
    if not model:
        raise ThemesEndpointError(
            "THEMES_LLM_MODEL must be set when THEMES_LLM_BASE_URL is"
        )
    if (
        scheme == "http"
        and not is_local_host(host)
        and not settings.THEMES_LLM_ALLOW_INSECURE_HTTP
    ):
        raise ThemesEndpointError(
            "plain http to a public address would send summaries and the key "
            "unencrypted; use https, a tunnel, or a private address"
        )
    return ThemesEndpoint(
        base_url=base,
        model=model,
        api_key=_api_key(),
        timeout=settings.THEMES_LLM_TIMEOUT_SECONDS,
        host=host,
    )
