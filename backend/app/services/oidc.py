"""Talking to an OpenID Connect provider for staff single sign-on (plan 15.10).

The flow is the authorization code flow with PKCE, run by the backend:

1. ``/auth/oidc/start`` sends the browser to the provider with a random ``state``,
   ``nonce`` and PKCE challenge (:func:`authorization_url`).
2. The provider sends the browser back to ``/auth/oidc/callback`` with a code, which
   the backend trades for an ID token over a direct, authenticated request
   (:func:`exchange_code`).
3. The ID token's signature is checked against the provider's published keys, and
   its issuer, audience, expiry and nonce against this attempt
   (:func:`verify_id_token`).

Only asymmetric signatures are accepted (never ``none`` or a shared-secret HMAC), so
a token cannot be forged with the client secret. The provider's discovery document
and keys are cached for an hour; an unknown key id triggers one refresh, which is how
a provider's key rotation is picked up.

Nothing here reads or writes the database. Which account a verified identity maps to
is :mod:`app.services.oidc_accounts`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt

from app.config import Settings, settings
from app.exceptions import OidcError
from app.models.user import UserRole

ALLOWED_ALGORITHMS: Final = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)
CLOCK_SKEW_SECONDS: Final = 60
DISCOVERY_TTL_SECONDS: Final = 3600.0
HTTP_TIMEOUT_SECONDS: Final = 10.0
SCOPES: Final = "openid email profile"
_LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class OidcConfig:
    """The provider settings this deployment uses."""

    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    dashboard_url: str
    label: str
    require_email_verified: bool
    role_claim: str
    role_map: dict[str, UserRole]


def _is_secure(url: str) -> bool:
    """HTTPS, or plain HTTP to this machine only (a local test provider)."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS


def _role_map(raw: str) -> dict[str, UserRole]:
    """Parse ``OIDC_ROLE_MAP``; an unusable map is a configuration error."""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OidcError("failed", "OIDC_ROLE_MAP is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise OidcError("failed", "OIDC_ROLE_MAP must be a JSON object")
    try:
        return {str(key): UserRole(value) for key, value in parsed.items()}
    except ValueError as exc:
        raise OidcError(
            "failed", "OIDC_ROLE_MAP values must be admin, hr or moderator"
        ) from exc


def load_config(source: Settings = settings) -> OidcConfig | None:
    """The provider settings, or ``None`` when SSO is not configured.

    Raises:
        OidcError: The settings are present but unsafe or malformed (a plain-HTTP
            provider, a role claim without a map).
    """
    issuer = source.OIDC_ISSUER.strip().rstrip("/")
    required = (
        issuer,
        source.OIDC_CLIENT_ID.strip(),
        source.OIDC_CLIENT_SECRET,
        source.OIDC_REDIRECT_URI.strip(),
        source.OIDC_DASHBOARD_URL.strip(),
    )
    if not all(required):
        return None
    for url in (issuer, source.OIDC_REDIRECT_URI, source.OIDC_DASHBOARD_URL):
        if not _is_secure(url.strip()):
            raise OidcError("failed", "OIDC URLs must use https")
    role_claim = source.OIDC_ROLE_CLAIM.strip()
    role_map = _role_map(source.OIDC_ROLE_MAP)
    if role_claim and not role_map:
        raise OidcError("failed", "OIDC_ROLE_CLAIM is set but OIDC_ROLE_MAP is empty")
    return OidcConfig(
        issuer=issuer,
        client_id=source.OIDC_CLIENT_ID.strip(),
        client_secret=source.OIDC_CLIENT_SECRET,
        redirect_uri=source.OIDC_REDIRECT_URI.strip(),
        dashboard_url=source.OIDC_DASHBOARD_URL.strip(),
        label=source.OIDC_PROVIDER_LABEL.strip() or "Single sign-on",
        require_email_verified=source.OIDC_REQUIRE_EMAIL_VERIFIED,
        role_claim=role_claim,
        role_map=role_map,
    )


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class OidcProvider:
    """One provider's discovery document and keys, cached, over an HTTP client."""

    def __init__(
        self,
        config: OidcConfig,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._client = client
        self._clock = clock
        self._discovery: dict[str, Any] | None = None
        self._discovery_at = 0.0
        self._jwks: dict[str, Any] | None = None

    async def _get_json(self, url: str) -> dict[str, Any]:
        if not _is_secure(url):
            raise OidcError("failed", "provider endpoint is not https")
        try:
            if self._client is not None:
                response = await self._client.get(url, timeout=HTTP_TIMEOUT_SECONDS)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError(
                "failed", f"provider request failed ({type(exc).__name__})"
            ) from exc
        if not isinstance(body, dict):
            raise OidcError(
                "failed", "provider returned something other than an object"
            )
        return body

    async def discovery(self) -> dict[str, Any]:
        """The provider's discovery document, checked to be for this issuer."""
        fresh = self._clock() - self._discovery_at < DISCOVERY_TTL_SECONDS
        if self._discovery is not None and fresh:
            return self._discovery
        document = await self._get_json(
            f"{self.config.issuer}/.well-known/openid-configuration"
        )
        if str(document.get("issuer", "")).rstrip("/") != self.config.issuer:
            raise OidcError("failed", "discovery document names a different issuer")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(document.get(field), str):
                raise OidcError("failed", f"discovery document has no {field}")
        self._discovery = document
        self._discovery_at = self._clock()
        self._jwks = None
        return document

    async def authorization_url(self, state: str, nonce: str, challenge: str) -> str:
        """Where to send the browser to sign in."""
        document = await self.discovery()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        endpoint = document["authorization_endpoint"]
        separator = "&" if "?" in endpoint else "?"
        return f"{endpoint}{separator}{query}"

    async def exchange_code(self, code: str, verifier: str) -> str:
        """Trade the callback's code for an ID token (client_secret_basic)."""
        document = await self.discovery()
        endpoint = document["token_endpoint"]
        if not _is_secure(endpoint):
            raise OidcError("failed", "token endpoint is not https")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "code_verifier": verifier,
        }
        # RFC 6749 2.3.1: both parts are form-encoded before Basic encoding.
        auth = httpx.BasicAuth(
            quote(self.config.client_id, safe=""),
            quote(self.config.client_secret, safe=""),
        )
        try:
            if self._client is not None:
                response = await self._client.post(
                    endpoint, data=form, auth=auth, timeout=HTTP_TIMEOUT_SECONDS
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        endpoint, data=form, auth=auth, timeout=HTTP_TIMEOUT_SECONDS
                    )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError(
                "failed", f"code exchange failed ({type(exc).__name__})"
            ) from exc
        token = body.get("id_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise OidcError("failed", "token response has no id_token")
        return token

    async def _signing_key(self, kid: str | None, algorithm: str) -> Any:
        """The provider key for *kid*, refreshing the key set once if unknown."""
        for attempt in range(2):
            if self._jwks is None or attempt == 1:
                document = await self.discovery()
                self._jwks = await self._get_json(document["jwks_uri"])
            for entry in self._jwks.get("keys", []):
                if not isinstance(entry, dict) or entry.get("use", "sig") != "sig":
                    continue
                if kid is not None and entry.get("kid") != kid:
                    continue
                if entry.get("alg") not in (None, algorithm):
                    continue
                try:
                    return jwt.PyJWK(entry, algorithm=algorithm).key
                except jwt.PyJWTError:
                    continue
        raise OidcError("failed", "no provider key matches the ID token")

    async def verify_id_token(self, token: str, nonce: str) -> dict[str, Any]:
        """Check *token*'s signature and claims; return the claims.

        Raises:
            OidcError: Bad signature, wrong issuer or audience, expired, or a nonce
                that is not this attempt's.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OidcError("failed", "ID token is not a JWT") from exc
        algorithm = header.get("alg")
        if algorithm not in ALLOWED_ALGORITHMS:
            raise OidcError("failed", "ID token uses a refused algorithm")
        key = await self._signing_key(header.get("kid"), algorithm)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                audience=self.config.client_id,
                issuer=self.config.issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(
                "failed", f"ID token rejected ({type(exc).__name__})"
            ) from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("failed", "ID token nonce does not match this sign-in")
        audience = claims.get("aud")
        shared = isinstance(audience, list) and len(audience) > 1
        if shared and claims.get("azp") != self.config.client_id:
            raise OidcError("failed", "ID token was issued to another client")
        return claims


_provider: OidcProvider | None = None


def get_provider() -> OidcProvider | None:
    """The process-wide provider, or ``None`` when SSO is off (a FastAPI dependency).

    Rebuilt when the settings change, so a test or a restart with new values does not
    keep a stale cache.
    """
    global _provider
    config = load_config()
    if config is None:
        _provider = None
        return None
    if _provider is None or _provider.config != config:
        _provider = OidcProvider(config)
    return _provider
