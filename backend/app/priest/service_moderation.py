"""The Guide's moderation calls on local Ollama, bounded so they cannot starve anything
(plan 14.2; split out of ``priest_service``).

A pass-path question is moderated beside retrieval and generation, on its own small
pool: the shared default pool also serves every other ``to_thread`` caller, and a
stuck Ollama must not be able to starve them (or itself). A failure or timeout counts
as ``policy``, never as a crisis verdict.

Deferral replies skip the rate limiter (never a 429 instead of help), so their check
gets a separate pool and a hard cap with no queue: a flood of deferral-phrased
questions can neither starve the pass path's moderation, where a crisis verdict
matters most, nor pile work onto Ollama. Past the cap a deferral goes out unmoderated,
as before this check existed.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Final

import httpx

from app.exceptions import AuriError
from app.models.confession import ModerationSeverity
from app.priest.service_run import ModeratorFn

logger = logging.getLogger(__name__)

MODERATION_CAP_SECONDS: Final = 5.0
_MODERATION_POOL: Final = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="priest-moderation"
)
DEFERRAL_CHECKS: Final = 2
_DEFERRAL_POOL: Final = ThreadPoolExecutor(
    max_workers=DEFERRAL_CHECKS, thread_name_prefix="priest-deferral-moderation"
)
_DEFERRAL_SLOTS: Final = threading.BoundedSemaphore(DEFERRAL_CHECKS)
# What a moderation call may raise; any of them counts as "policy", never "crisis".
_MODERATION_ERRORS: Final = (
    AuriError,
    httpx.HTTPError,
    OSError,
    ValueError,
    RuntimeError,
    LookupError,
    TypeError,
)


async def moderate(moderator: ModeratorFn, text: str, cap: float) -> ModerationSeverity:
    """Moderate on local Ollama in a thread; a failure or timeout is policy."""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_MODERATION_POOL, moderator, text),
            timeout=cap,
        )
    except TimeoutError:
        logger.warning("priest moderation timed out; counting it as policy")
    except _MODERATION_ERRORS as exc:
        logger.warning(
            "priest moderation failed (%s); counting it as policy",
            type(exc).__name__,
        )
    return ModerationSeverity.policy


async def moderate_deferral(
    moderator: ModeratorFn, text: str, cap: float
) -> ModerationSeverity:
    """Moderate a deferral question if a check slot is free; never queue for one."""
    if not _DEFERRAL_SLOTS.acquire(blocking=False):
        logger.info("priest deferral moderation skipped: all checks busy")
        return ModerationSeverity.none
    # The slot is returned when the thread really finishes, not when we stop
    # waiting for it, so a hung moderator still counts against the cap.
    job = _DEFERRAL_POOL.submit(moderator, text)
    job.add_done_callback(lambda _job: _DEFERRAL_SLOTS.release())
    try:
        return await asyncio.wait_for(asyncio.wrap_future(job), timeout=cap)
    except TimeoutError:
        logger.warning("priest deferral moderation timed out; deferring")
    except _MODERATION_ERRORS as exc:
        logger.warning(
            "priest deferral moderation failed (%s); deferring", type(exc).__name__
        )
    return ModerationSeverity.none
