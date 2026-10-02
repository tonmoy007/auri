"""Sliding-window limits for priest questions, per device and per client address.

The device header is chosen by the client, so a client that varies it gets a fresh
device limit each time. When the deployment names a trusted proxy header
(``TRUSTED_PROXY_HEADER``, plan 14.7), the client's address gets its own, wider
window as well, and a question must fit both. Fixed replies (crisis, deferral,
unsupported script) are never held to the question limits, so a person in distress is
never refused help by them; they have a separate, high ceiling so a flood of them
still has a cost bound.

Every window is keyed by ``HMAC(per-process random salt, scope + value)``, so neither
the raw device code nor an address is ever a dict key or kept beyond the call; the
salt dies with the process, so the keys cannot be matched to anything afterwards.
State is only hit times (counts in effect), pruned after a day and capped in size. It
is in-memory and single-instance, like the STT and TTS limits; the ADR records that.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.exceptions import RateLimitError
from app.priest import priest_config

MINUTE_SECONDS: Final = 60.0
DAY_SECONDS: Final = 86400.0
MAX_TRACKED_KEYS: Final = 50_000
_SALT_BYTES: Final = 32


@dataclass(frozen=True)
class Limits:
    """How many hits a minute and a day one key may make."""

    per_minute: int
    per_day: int


# The ceiling for fixed replies. They cost a lexicon pass (a crafted question can cost
# about 60 ms of CPU, privacy review finding 56) and no model call, so the ceiling only
# has to stop a flood: no person in distress sends twenty messages a minute. The
# address ceiling is wider because a whole office can share one address.
FIXED_REPLY_DEVICE_LIMITS: Final = Limits(per_minute=20, per_day=200)
FIXED_REPLY_ADDRESS_LIMITS: Final = Limits(per_minute=100, per_day=2000)


def _question_device_limits() -> Limits:
    return Limits(
        priest_config.rate_limit_per_minute(), priest_config.rate_limit_per_day()
    )


def _question_address_limits() -> Limits:
    return Limits(
        priest_config.rate_limit_per_ip_per_minute(),
        priest_config.rate_limit_per_ip_per_day(),
    )


class PriestRateLimitError(RateLimitError):
    """Raised when a device asks the guide too often.

    The message is fixed on purpose: the handler shows it, and it must never carry a
    device code or a question.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate_limited")
        self.retry_after_seconds = retry_after_seconds


class PriestRateLimiter:
    """Per-minute and per-day windows per device and address, over an injectable clock."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_TRACKED_KEYS,
    ) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._salt = secrets.token_bytes(_SALT_BYTES)
        # Least recently used first, so pruning and eviction both pop from the front.
        self._hits: OrderedDict[bytes, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        """How many keys (devices and addresses) are being tracked."""
        return len(self._hits)

    def _key(self, scope: str, value: str) -> bytes:
        # The scope keeps a device and an address, or a question and a fixed reply,
        # from ever sharing a window.
        return hmac.new(
            self._salt, f"{scope}\0{value}".encode(), hashlib.sha256
        ).digest()

    def check_and_record(
        self,
        device_token_hash: str,
        client_address: str | None = None,
        *,
        fixed_reply: bool = False,
    ) -> None:
        """Count one hit for the device (and the address, when known), or raise.

        The hit must fit every window that applies; it is recorded in all of them or
        in none. A refused hit is not recorded, so asking again while blocked does
        not push the end of the block further out.

        Args:
            device_token_hash: The device header.
            client_address: The client's address from the trusted proxy header, or
                ``None`` when the deployment names none (no address window then).
            fixed_reply: Count against the high fixed-reply ceiling instead of the
                question limits.

        Raises:
            PriestRateLimitError: Over a limit, carrying the whole seconds to wait
                (the longest wait among the windows that are full).
        """
        if fixed_reply:
            scopes = [("fixed-device", device_token_hash, FIXED_REPLY_DEVICE_LIMITS)]
            if client_address is not None:
                scopes.append(
                    ("fixed-address", client_address, FIXED_REPLY_ADDRESS_LIMITS)
                )
        else:
            scopes = [("device", device_token_hash, _question_device_limits())]
            if client_address is not None:
                scopes.append(("address", client_address, _question_address_limits()))
        keyed = [(self._key(scope, value), limits) for scope, value, limits in scopes]
        with self._lock:
            now = self._clock()
            self._drop_idle(now)
            windows = [
                (key, self._hits.get(key, deque()), limits) for key, limits in keyed
            ]
            wait = max(
                (
                    self._wait_seconds(window, now, limits)
                    for _, window, limits in windows
                ),
                default=0,
            )
            if wait:
                raise PriestRateLimitError(wait)
            for key, window, _ in windows:
                window.append(now)
                self._remember(key, window)

    def _drop_idle(self, now: float) -> None:
        """Forget keys that have not been hit for a day.

        An empty window counts as idle (defensive: keys are ordered by their last
        hit, so a window whose hits have all aged out is dropped here before
        anything trims it).
        """
        while self._hits:
            oldest_key = next(iter(self._hits))
            window = self._hits[oldest_key]
            if window and window[-1] > now - DAY_SECONDS:
                return
            del self._hits[oldest_key]

    def _remember(self, key: bytes, window: deque[float]) -> None:
        """Mark *key* as most recent, dropping the oldest keys beyond the cap."""
        self._hits[key] = window
        self._hits.move_to_end(key)
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)

    @staticmethod
    def _trim(window: deque[float], now: float) -> None:
        while window and window[0] <= now - DAY_SECONDS:
            window.popleft()

    def _wait_seconds(self, window: deque[float], now: float, limits: Limits) -> int:
        """Seconds until one more hit is allowed, or 0 if it is allowed now."""
        self._trim(window, now)
        return max(
            _wait_for(window, now, MINUTE_SECONDS, limits.per_minute),
            _wait_for(window, now, DAY_SECONDS, limits.per_day),
        )


def _wait_for(window: deque[float], now: float, span: float, limit: int) -> int:
    """Whole seconds until the hit *limit* places back leaves a *span* window.

    Returns 0 when fewer than *limit* hits fall inside the window. A hit exactly
    *span* old has already left it.
    """
    inside = sum(1 for hit in reversed(window) if hit > now - span)
    if inside < limit:
        return 0
    blocking = window[len(window) - limit]
    return max(1, math.ceil(blocking + span - now))


_default_limiter = PriestRateLimiter()


def get_rate_limiter() -> PriestRateLimiter:
    """The process-wide limiter, for use as a FastAPI dependency."""
    return _default_limiter
