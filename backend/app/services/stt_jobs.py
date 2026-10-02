"""In-memory background transcription jobs (plan task 16.1).

A five-minute recording takes Whisper several minutes, far longer than a request
should stay open. In job mode the upload returns a job id at once and the app
polls for the result. Jobs live in this process only (single instance, like the
STT rate-limit store), carry no audio, are visible only to the device that made
them, are deleted on their first successful read, and expire if nobody reads
them. The number of jobs waiting at once is capped so uploads cannot pile work
onto the transcriber without bound.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

PENDING: Final = "pending"
READY: Final = "ready"
FAILED: Final = "failed"


class JobStoreFull(Exception):
    """Too many jobs are already waiting; the caller should retry later."""


@dataclass(frozen=True)
class Job:
    """One transcription job and, once finished, its outcome."""

    device: str
    created_at: datetime
    status: str = PENDING
    transcript: str | None = None
    detail: str | None = None
    finished_at: datetime | None = None


class JobStore:
    """Thread-safe store of jobs keyed by an unguessable id."""

    def __init__(
        self,
        max_open: int = 8,
        ttl: timedelta = timedelta(minutes=10),
        max_pending: timedelta = timedelta(minutes=25),
    ) -> None:
        """Create a store.

        Args:
            max_open: Jobs that may be waiting at once.
            ttl: How long a finished job waits to be read before it is dropped.
            max_pending: How long a job may stay unfinished before it is dropped
                (longer than the app's own 20-minute ceiling).
        """
        self.max_open = max_open
        self._ttl = ttl
        self._max_pending = max_pending
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    def clear(self) -> None:
        """Forget every job (tests, and a process that is shutting down)."""
        with self._lock:
            self._jobs.clear()

    def _sweep(self, now: datetime) -> None:
        """Drop finished jobs past their TTL and jobs pending for too long."""
        for job_id, job in list(self._jobs.items()):
            done_too_long = (
                job.finished_at is not None and now - job.finished_at > self._ttl
            )
            stuck = job.status == PENDING and now - job.created_at > self._max_pending
            if done_too_long or stuck:
                del self._jobs[job_id]

    def create(self, device: str, now: datetime) -> str:
        """Open a job for *device* and return its id.

        Raises:
            JobStoreFull: If ``max_open`` jobs are already pending.
        """
        with self._lock:
            self._sweep(now)
            if (
                sum(job.status == PENDING for job in self._jobs.values())
                >= self.max_open
            ):
                raise JobStoreFull
            job_id = secrets.token_urlsafe(18)
            self._jobs[job_id] = Job(device=device, created_at=now)
            return job_id

    def _settle(
        self,
        job_id: str,
        now: datetime,
        status: str,
        transcript: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                self._jobs[job_id] = replace(
                    job,
                    status=status,
                    transcript=transcript,
                    detail=detail,
                    finished_at=now,
                )

    def finish(self, job_id: str, transcript: str, now: datetime) -> None:
        """Record a successful transcript."""
        self._settle(job_id, now, READY, transcript=transcript)

    def fail(self, job_id: str, detail: str, now: datetime) -> None:
        """Record a failure by a fixed code, never by an error message."""
        self._settle(job_id, now, FAILED, detail=detail)

    def take(self, job_id: str, device: str, now: datetime) -> Job | None:
        """The job if *device* owns it; a finished job is removed as it is read.

        Returns ``None`` for an unknown, expired or someone else's job, so a wrong
        device cannot tell those cases apart.
        """
        with self._lock:
            self._sweep(now)
            job = self._jobs.get(job_id)
            if job is None or not secrets.compare_digest(job.device, device):
                return None
            if job.status != PENDING:
                del self._jobs[job_id]
            return job

    def track(self, task: asyncio.Task[None]) -> None:
        """Keep a reference to a running job so it is not garbage-collected."""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self, timeout: float) -> None:
        """Wait up to *timeout* seconds for running jobs (tests and shutdown)."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)
        else:
            await asyncio.sleep(timeout)
