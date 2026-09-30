"""A device-scoped sliding-window limiter for priest questions.

It is keyed by ``HMAC(per-process random salt, device hash)`` so the raw device code is
never a dict key and is not kept beyond the call; the salt dies with the process, so
the keys cannot be matched to anything afterwards. State is only hit times (counts in
effect), pruned after a day and capped in size. It is in-memory and single-instance,
like the STT and TTS limits; the ADR records that.
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
from typing import Final

from app.exceptions import RateLimitError
from app.priest import priest_config

MINUTE_SECONDS: Final = 60.0
DAY_SECONDS: Final = 86400.0
MAX_TRACKED_KEYS: Final = 50_000
_SALT_BYTES: Final = 32


class PriestRateLimitError(RateLimitError):
    """Raised when a device asks the guide too often.

    The message is fixed on purpose: the handler shows it, and it must never carry a
    device code or a question.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate_limited")
        self.retry_after_seconds = retry_after_seconds


class PriestRateLimiter:
    """Per-device per-minute and per-day windows over an injectable clock."""

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
        """How many devices are being tracked."""
        return len(self._hits)

    def _key(self, device_token_hash: str) -> bytes:
        return hmac.new(
            self._salt, device_token_hash.encode("utf-8"), hashlib.sha256
        ).digest()

    def check_and_record(self, device_token_hash: str) -> None:
        """Count one question for the device, or raise if it is over a limit.

        A refused question is not recorded, so asking again while blocked does not
        push the end of the block further out.

        Raises:
            PriestRateLimitError: Over the per-minute or per-day limit, carrying the
                whole seconds to wait.
        """
        key = self._key(device_token_hash)
        with self._lock:
            now = self._clock()
            self._drop_idle(now)
            window = self._hits.get(key)
            if window is None:
                window = deque()
            wait = self._wait_seconds(window, now)
            if wait:
                raise PriestRateLimitError(wait)
            window.append(now)
            self._remember(key, window)

    def _drop_idle(self, now: float) -> None:
        """Forget devices that have not asked for a day."""
        while self._hits:
            oldest_key = next(iter(self._hits))
            if self._hits[oldest_key][-1] > now - DAY_SECONDS:
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

    def _wait_seconds(self, window: deque[float], now: float) -> int:
        """Seconds until one more hit is allowed, or 0 if it is allowed now."""
        self._trim(window, now)
        waits = [
            wait
            for wait in (
                _wait_for(
                    window, now, MINUTE_SECONDS, priest_config.rate_limit_per_minute()
                ),
                _wait_for(window, now, DAY_SECONDS, priest_config.rate_limit_per_day()),
            )
            if wait
        ]
        return max(waits, default=0)


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
