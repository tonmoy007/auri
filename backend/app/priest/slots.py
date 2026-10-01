"""The Guide's in-flight limit, read fresh on every request.

``asyncio.Semaphore`` fixes its size when it is built, so ``PRIEST_MAX_CONCURRENCY``
used to need a restart (privacy review row 44). This counter reads the limit each
time a question asks for a slot: a raised limit applies to the very next question,
and a lowered one never cancels a question already running. New questions simply
wait until the count is below the new limit.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class LiveSlots:
    """Questions in flight, against a limit that can change at any time."""

    def __init__(self, limit: Callable[[], int]) -> None:
        """Create the counter.

        Args:
            limit: Returns the current limit; called on every acquire and release.
        """
        self._limit = limit
        self._in_flight = 0
        self._changed = asyncio.Condition()

    @property
    def in_flight(self) -> int:
        """Questions holding a slot right now."""
        return self._in_flight

    async def acquire(self) -> None:
        """Wait until a slot is free under the current limit, then take it."""
        async with self._changed:
            await self._changed.wait_for(lambda: self._in_flight < self._limit())
            self._in_flight += 1

    async def release(self) -> None:
        """Give a slot back and wake anyone waiting for one."""
        async with self._changed:
            self._in_flight -= 1
            self._changed.notify_all()
