"""In-process login attempt throttle.

Password endpoints are the one place in Auri where an attacker gets
unlimited free guesses against a real credential, so failed attempts are
counted per email over a sliding window and the account's login endpoint
locks out once the limit is hit.

State is per-process and deliberately not persisted: a restart clearing the
counters is acceptable, a shared table on the login hot path is not. Run
behind a single API process, or add a shared store if that changes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_failed_attempts: dict[str, list[datetime]] = defaultdict(list)


def _recent_attempts(email: str, now: datetime, window_seconds: int) -> list[datetime]:
    """Return *email*'s failures inside the window, discarding older ones."""
    cutoff = now - timedelta(seconds=window_seconds)
    recent = [moment for moment in _failed_attempts[email] if moment > cutoff]
    _failed_attempts[email] = recent
    return recent


def is_locked_out(
    email: str, now: datetime, max_attempts: int, window_seconds: int
) -> bool:
    """Return ``True`` if *email* has used up its attempts inside the window."""
    return len(_recent_attempts(email, now, window_seconds)) >= max_attempts


def record_failure(email: str, now: datetime, window_seconds: int) -> None:
    """Record a failed login for *email* at *now*."""
    recent = _recent_attempts(email, now, window_seconds)
    recent.append(now)
    _failed_attempts[email] = recent


def clear(email: str) -> None:
    """Forget *email*'s failures — called after a successful login."""
    _failed_attempts.pop(email, None)


def reset_all() -> None:
    """Drop every counter. Used by tests to keep cases independent (§16.3)."""
    _failed_attempts.clear()
