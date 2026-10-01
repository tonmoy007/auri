"""Short-lived single sign-on state: sign-in attempts and session handovers.

Two in-memory stores, one per process, like the other short-lived stores here
(``stt_jobs``, the rate limiters); a deployment with several API processes would
need a shared store or sticky sessions for SSO.

- **Attempts.** ``/start`` opens one with a random ``state``, keeping the nonce
  and PKCE verifier that only the backend may know. ``/callback`` takes it once:
  a replayed or forged ``state`` finds nothing. Attempts expire after 10 minutes.
- **Handovers.** After a successful callback the browser goes back to the dashboard
  with a one-time code in the URL fragment (never sent to a server or written to
  an access log). The dashboard trades it once, within a minute, for session
  tokens. Session tokens therefore never appear in a URL.

Codes are 256-bit random values; neither store holds anything about a person
beyond the account id waiting to be handed over.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from dataclasses import dataclass
from typing import Final

ATTEMPT_TTL_SECONDS: Final = 600.0
HANDOVER_TTL_SECONDS: Final = 60.0
MAX_OPEN_ATTEMPTS: Final = 1000
MAX_OPEN_HANDOVERS: Final = 1000


@dataclass(frozen=True)
class Attempt:
    """What the callback needs from the start of a sign-in."""

    nonce: str
    verifier: str
    expires_at: float


@dataclass(frozen=True)
class Handover:
    """An account waiting for the dashboard to collect its session."""

    user_id: uuid.UUID
    expires_at: float


class OidcSessionStore:
    """Single-use attempts and handovers with expiry, over a caller's clock."""

    def __init__(self) -> None:
        self._attempts: dict[str, Attempt] = {}
        self._handovers: dict[str, Handover] = {}
        self._lock = threading.Lock()

    def open_attempt(self, nonce: str, verifier: str, now: float) -> str:
        """Store a new attempt and return its ``state``."""
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._drop_expired(now)
            _evict_oldest(self._attempts, MAX_OPEN_ATTEMPTS)
            self._attempts[state] = Attempt(nonce, verifier, now + ATTEMPT_TTL_SECONDS)
        return state

    def take_attempt(self, state: str, now: float) -> Attempt | None:
        """The attempt for *state*, removed; ``None`` if unknown or expired."""
        with self._lock:
            attempt = self._attempts.pop(state, None)
        if attempt is None or attempt.expires_at <= now:
            return None
        return attempt

    def open_handover(self, user_id: uuid.UUID, now: float) -> str:
        """Store a handover for *user_id* and return its one-time code."""
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._drop_expired(now)
            _evict_oldest(self._handovers, MAX_OPEN_HANDOVERS)
            self._handovers[code] = Handover(user_id, now + HANDOVER_TTL_SECONDS)
        return code

    def take_handover(self, code: str, now: float) -> uuid.UUID | None:
        """The account for *code*, removed; ``None`` if unknown or expired."""
        with self._lock:
            handover = self._handovers.pop(code, None)
        if handover is None or handover.expires_at <= now:
            return None
        return handover.user_id

    def clear(self) -> None:
        """Forget everything (tests)."""
        with self._lock:
            self._attempts.clear()
            self._handovers.clear()

    def _drop_expired(self, now: float) -> None:
        for store in (self._attempts, self._handovers):
            for key in [k for k, v in store.items() if v.expires_at <= now]:
                del store[key]


def _evict_oldest(store: dict[str, Attempt] | dict[str, Handover], cap: int) -> None:
    """Keep room for one more entry; dicts keep insertion order, oldest first."""
    while len(store) >= cap:
        del store[next(iter(store))]


oidc_sessions = OidcSessionStore()
