"""A fake OpenID Connect provider for single sign-on tests (plan 15.10).

It serves a discovery document, a key set and a token endpoint over an
``httpx.MockTransport``, and signs ID tokens with a freshly generated RSA key, so
the real verification code runs end to end without a network.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from app.services.oidc import OidcConfig, OidcProvider
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.example.com"
CLIENT_ID = "auri-dashboard"
CLIENT_SECRET = "s3cret:with/odd chars"
REDIRECT_URI = "http://localhost/api/v1/auth/oidc/callback"
DASHBOARD_URL = "http://localhost:5173/"


def make_config(**overrides: Any) -> OidcConfig:
    values: dict[str, Any] = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "dashboard_url": DASHBOARD_URL,
        "label": "Company SSO",
        "require_email_verified": True,
        "role_claim": "",
        "role_map": {},
    }
    values.update(overrides)
    return OidcConfig(**values)


class FakeIdp:
    """The provider side: keys, issued tokens and the requests it received."""

    def __init__(self, issuer: str = ISSUER) -> None:
        self.issuer = issuer
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.claims: dict[str, Any] = {}
        self.token_requests: list[httpx.Request] = []
        self.jwks_fetches = 0
        self.id_token: str | None = None
        self.discovery_issuer = issuer

    def rotate(self) -> None:
        """Start signing with a new key under a new key id."""
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = f"key-{int(time.time() * 1000)}"

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def sign(self, **claims: Any) -> str:
        now = int(time.time())
        payload = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "sub": "user-123",
            "iat": now,
            "exp": now + 300,
            "email": "hr@example.com",
            "email_verified": True,
        }
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        return jwt.encode(
            payload, self.key, algorithm="RS256", headers={"kid": self.kid}
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": self.discovery_issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/jwks",
                },
            )
        if path == "/jwks":
            self.jwks_fetches += 1
            return httpx.Response(200, json=self.jwks())
        if path == "/token":
            self.token_requests.append(request)
            if self.id_token is not None:
                return httpx.Response(200, json={"id_token": self.id_token})
            form = parse_qs(request.content.decode())
            del form  # the code is not checked; the nonce comes from self.claims
            return httpx.Response(200, json={"id_token": self.sign(**self.claims)})
        return httpx.Response(404)

    def provider(self, config: OidcConfig | None = None) -> OidcProvider:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return OidcProvider(config or make_config(), client=client)
