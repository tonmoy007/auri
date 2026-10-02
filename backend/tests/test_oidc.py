"""Tests for talking to an OpenID Connect provider (plan 15.10)."""

from __future__ import annotations

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from app.exceptions import OidcError
from app.models.user import UserRole
from app.services.oidc import load_config, pkce_pair

from tests.conftest import SettingPatcher
from tests.fake_oidc import CLIENT_ID, ISSUER, FakeIdp


def _configure(set_setting: SettingPatcher, **overrides: str) -> None:
    values = {
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT_ID,
        "OIDC_CLIENT_SECRET": "secret",
        "OIDC_REDIRECT_URI": "https://auri.example.com/api/v1/auth/oidc/callback",
        "OIDC_DASHBOARD_URL": "https://dashboard.example.com/",
        "OIDC_ROLE_CLAIM": "",
        "OIDC_ROLE_MAP": "",
    }
    values.update(overrides)
    for key, value in values.items():
        set_setting(key, value)


# ── configuration ────────────────────────────────────────────────────────────


def test_sso_is_off_until_every_required_setting_is_present(
    set_setting: SettingPatcher,
) -> None:
    _configure(set_setting, OIDC_CLIENT_SECRET="")
    assert load_config() is None


def test_a_complete_configuration_is_loaded(set_setting: SettingPatcher) -> None:
    _configure(set_setting, OIDC_ISSUER=f"{ISSUER}/")
    config = load_config()
    assert config is not None
    assert config.issuer == ISSUER  # trailing slash dropped


@pytest.mark.parametrize(
    "key", ["OIDC_ISSUER", "OIDC_REDIRECT_URI", "OIDC_DASHBOARD_URL"]
)
def test_plain_http_is_refused_except_to_this_machine(
    set_setting: SettingPatcher, key: str
) -> None:
    _configure(set_setting, **{key: "http://example.com/x"})
    with pytest.raises(OidcError):
        load_config()


def test_plain_http_to_localhost_is_allowed_for_a_local_provider(
    set_setting: SettingPatcher,
) -> None:
    _configure(set_setting, OIDC_ISSUER="http://localhost:8080/realms/auri")
    assert load_config() is not None


def test_a_role_claim_without_a_map_is_a_configuration_error(
    set_setting: SettingPatcher,
) -> None:
    _configure(set_setting, OIDC_ROLE_CLAIM="groups")
    with pytest.raises(OidcError):
        load_config()


@pytest.mark.parametrize("raw", ["not json", "[]", '{"g": "superuser"}'])
def test_an_unusable_role_map_is_a_configuration_error(
    set_setting: SettingPatcher, raw: str
) -> None:
    _configure(set_setting, OIDC_ROLE_CLAIM="groups", OIDC_ROLE_MAP=raw)
    with pytest.raises(OidcError):
        load_config()


def test_the_role_map_is_parsed(set_setting: SettingPatcher) -> None:
    _configure(set_setting, OIDC_ROLE_CLAIM="groups", OIDC_ROLE_MAP='{"auri-hr": "hr"}')
    config = load_config()
    assert config is not None
    assert config.role_map == {"auri-hr": UserRole.hr}


# ── PKCE and the sign-in URL ─────────────────────────────────────────────────


def test_the_pkce_challenge_is_the_s256_of_the_verifier() -> None:
    verifier, challenge = pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert 43 <= len(verifier) <= 128


@pytest.mark.asyncio
async def test_the_sign_in_url_carries_state_nonce_and_challenge() -> None:
    idp = FakeIdp()
    url = await idp.provider().authorization_url("st", "no", "ch")
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{ISSUER}/authorize"
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["state"] == "st"
    assert query["nonce"] == "no"
    assert query["code_challenge"] == "ch"
    assert query["code_challenge_method"] == "S256"
    assert "openid" in query["scope"].split()


@pytest.mark.asyncio
async def test_a_discovery_document_for_another_issuer_is_refused() -> None:
    idp = FakeIdp()
    idp.discovery_issuer = "https://evil.example.com"
    with pytest.raises(OidcError):
        await idp.provider().discovery()


# ── the code exchange ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_code_exchange_authenticates_and_sends_the_verifier() -> None:
    idp = FakeIdp()
    idp.claims = {"nonce": "n"}

    token = await idp.provider().exchange_code("the-code", "the-verifier")

    assert token
    request = idp.token_requests[0]
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert form["code"] == "the-code"
    assert form["code_verifier"] == "the-verifier"
    assert form["grant_type"] == "authorization_code"
    assert "client_secret" not in form  # sent as Basic auth, form-encoded first
    scheme, _, value = request.headers["Authorization"].partition(" ")
    assert scheme == "Basic"
    user, _, password = base64.b64decode(value).decode().partition(":")
    assert user == CLIENT_ID
    assert password == "s3cret%3Awith%2Fodd%20chars"


@pytest.mark.asyncio
async def test_a_token_response_without_an_id_token_is_refused() -> None:
    idp = FakeIdp()
    idp.id_token = ""
    with pytest.raises(OidcError):
        await idp.provider().exchange_code("c", "v")


# ── ID token verification ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_valid_id_token_is_accepted() -> None:
    idp = FakeIdp()
    claims = await idp.provider().verify_id_token(idp.sign(nonce="n"), "n")
    assert claims["sub"] == "user-123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"nonce": "another-sign-in"},
        {"nonce": "n", "aud": "someone-else"},
        {"nonce": "n", "iss": "https://evil.example.com"},
        {"nonce": "n", "exp": int(time.time()) - 3600},
        {"nonce": "n", "aud": [CLIENT_ID, "other"], "azp": "other"},
        {"nonce": None},
    ],
    ids=["nonce", "audience", "issuer", "expired", "azp", "no-nonce"],
)
async def test_a_wrong_id_token_is_refused(claims: dict[str, object]) -> None:
    idp = FakeIdp()
    with pytest.raises(OidcError):
        await idp.provider().verify_id_token(idp.sign(**claims), "n")


@pytest.mark.asyncio
async def test_a_token_signed_with_another_key_is_refused() -> None:
    idp = FakeIdp()
    provider = idp.provider()
    forger = FakeIdp()  # same key id, different key
    with pytest.raises(OidcError):
        await provider.verify_id_token(forger.sign(nonce="n"), "n")


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", ["HS256", "none"])
async def test_symmetric_and_unsigned_tokens_are_refused(algorithm: str) -> None:
    # A client secret, or no key at all, must never be enough to mint a sign-in
    idp = FakeIdp()
    payload = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "x",
        "nonce": "n",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    key = "a-client-secret-long-enough-for-hs256" if algorithm == "HS256" else ""
    token = jwt.encode(payload, key, algorithm=algorithm)
    with pytest.raises(OidcError):
        await idp.provider().verify_id_token(token, "n")


@pytest.mark.asyncio
async def test_a_rotated_key_is_picked_up_with_one_refresh() -> None:
    idp = FakeIdp()
    provider = idp.provider()
    await provider.verify_id_token(idp.sign(nonce="n"), "n")
    assert idp.jwks_fetches == 1

    idp.rotate()
    claims = await provider.verify_id_token(idp.sign(nonce="n"), "n")

    assert claims["sub"] == "user-123"
    assert idp.jwks_fetches == 2
