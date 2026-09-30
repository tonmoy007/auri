"""Blocking real network connections in tests, and the opt-in for live model tests."""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

LIVE_ENV: Final = "RUN_LIVE_LLM"


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to open a real network connection."""

    def __init__(self, target: str) -> None:
        super().__init__(
            f"test tried to connect to {target}: tests must not use the network or a real "
            "model. Mock the call, or mark a deliberate live test with "
            f"@pytest.mark.live_llm and run it with {LIVE_ENV}=1."
        )


def live_tests_enabled() -> bool:
    """Whether live model tests were asked for (``RUN_LIVE_LLM=1``)."""
    return os.environ.get(LIVE_ENV, "").strip() in {"1", "true", "yes"}


def _target(address: Any) -> str | None:
    """Return ``host:port`` for an internet address, or ``None`` for a local socket."""
    if isinstance(address, tuple) and len(address) >= 2:
        port = address[1]
        if not isinstance(port, int) or not 0 <= port <= 65535:
            return None  # cannot connect anywhere; the real call raises on its own
        return f"{address[0]}:{port}"
    return None


@contextmanager
def block_network(violations: list[str]) -> Iterator[None]:
    """Refuse internet connections for the duration, recording each attempt.

    Unix sockets (used by asyncio itself) are left alone.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: Any) -> None:
        target = _target(address)
        if target is None:
            return real_connect(self, address)
        violations.append(target)
        raise NetworkBlockedError(target)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        target = _target(address)
        if target is None:
            return real_connect_ex(self, address)
        violations.append(target)
        raise NetworkBlockedError(target)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
