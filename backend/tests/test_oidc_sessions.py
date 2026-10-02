"""Tests for single sign-on attempts and handovers (plan 15.10)."""

from __future__ import annotations

import uuid

from app.services import oidc_sessions as module
from app.services.oidc_sessions import (
    ATTEMPT_TTL_SECONDS,
    HANDOVER_TTL_SECONDS,
    OidcSessionStore,
)


def test_an_attempt_is_taken_once() -> None:
    store = OidcSessionStore()
    state = store.open_attempt("nonce", "verifier", now=0.0)

    first = store.take_attempt(state, now=1.0)
    second = store.take_attempt(state, now=1.0)

    assert first is not None
    assert (first.nonce, first.verifier) == ("nonce", "verifier")
    assert second is None


def test_an_unknown_or_expired_attempt_is_nothing() -> None:
    store = OidcSessionStore()
    state = store.open_attempt("n", "v", now=0.0)
    assert store.take_attempt("forged", now=1.0) is None
    assert store.take_attempt(state, now=ATTEMPT_TTL_SECONDS) is None


def test_a_handover_is_taken_once_within_its_minute() -> None:
    store = OidcSessionStore()
    user_id = uuid.uuid4()
    code = store.open_handover(user_id, now=0.0)

    assert store.take_handover(code, now=HANDOVER_TTL_SECONDS - 1) == user_id
    assert store.take_handover(code, now=HANDOVER_TTL_SECONDS - 1) is None


def test_an_expired_handover_is_nothing() -> None:
    store = OidcSessionStore()
    code = store.open_handover(uuid.uuid4(), now=0.0)
    assert store.take_handover(code, now=HANDOVER_TTL_SECONDS) is None


def test_codes_are_long_and_random() -> None:
    store = OidcSessionStore()
    codes = {store.open_handover(uuid.uuid4(), now=0.0) for _ in range(50)}
    assert len(codes) == 50
    assert all(len(code) >= 43 for code in codes)


def test_the_stores_are_bounded(monkeypatch) -> None:
    monkeypatch.setattr(module, "MAX_OPEN_ATTEMPTS", 3)
    store = OidcSessionStore()
    states = [store.open_attempt("n", "v", now=0.0) for _ in range(5)]

    assert store.take_attempt(states[0], now=1.0) is None  # oldest evicted
    assert store.take_attempt(states[-1], now=1.0) is not None
