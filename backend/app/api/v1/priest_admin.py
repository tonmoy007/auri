"""Admin API for priest mode: the index, its builds, service health and usage.

Every route needs an ``admin`` session or the legacy ``X-Admin-Api-Key`` (see
``app.api.deps.require_admin_access``). Nothing here returns note text, a question
or an answer: the index view is counts and hashes, health is reachability only, usage
is suppressed counts, and the report is whatever summary the eval scripts wrote.

Reindex and activate are audited, by name for a session and under the label
``admin-api-key`` for the shared key, which names nobody. ``audit_service.record``
commits, so it is always the last database call, after the change it accounts for.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_access
from app.config import settings
from app.database import session_dependency
from app.exceptions import PriestEndpointError, PriestIndexError, ThemesEndpointError
from app.llm import chat_endpoint
from app.llm.chat_endpoint import ChatEndpoint
from app.models.audit_event import AuditAction
from app.models.user import User
from app.priest import build_status, index_store, metrics, priest_config
from app.priest.build_status import BuildState, BuildStatus
from app.priest.index_store import IndexManifest
from app.services import audit_service, insights_service, themes_endpoint

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/priest",
    tags=["priest-admin"],
    dependencies=[Depends(require_admin_access)],
)

PROBE_TIMEOUT_SECONDS: Final = 3.0
MAX_REPORT_BYTES: Final = 64 * 1024
_REPORTS_DIR: Final = "reports"
_BUILDER_MODULE: Final = "app.priest.index_builder"
# repo/backend/app/api/v1/priest_admin.py -> repo/backend
_BACKEND_DIR: Final = Path(__file__).resolve().parents[3]
# The shape index_store accepts for a version name: nothing that can leave the
# versions directory (no separator, no leading dot).
_VERSION_PATTERN: Final = r"^[0-9A-Za-z][0-9A-Za-z._-]*$"
_MAX_VERSION_CHARS: Final = 64

BuilderLauncher = Callable[[], int]


# ── Response and request models ──────────────────────────────────────────


class ManifestSummary(BaseModel):
    """The manifest facts safe to show: counts and versions, no note paths or text."""

    version: str
    created_at: str
    embed_model: str
    embed_digest: str
    dim: int
    chunk_count: int
    note_count: int
    exclusions: dict[str, int]
    unresolved_links: int
    warnings: list[str]
    build_seconds: float
    cleaner_version: str
    chunker_version: str


class IndexResponse(BaseModel):
    """The active index, the versions kept, and the model the next build will use."""

    active_version: str | None
    manifest: ManifestSummary | None
    versions: list[str]
    configured_embed_model: str


class ReindexResponse(BaseModel):
    """A build was started."""

    state: Literal["running"]
    started_at: str


class BuildStatusResponse(BaseModel):
    """The build status file, as ``build_status.BuildStatus.to_json`` writes it."""

    state: str
    started_at: str | None
    finished_at: str | None
    pid: int | None
    notes_total: int
    notes_indexed: int
    chunks: int
    exclusions: dict[str, int]
    error_code: str | None


class ActivateRequest(BaseModel):
    """The version to serve; for rolling back to an older build."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=_VERSION_PATTERN, max_length=_MAX_VERSION_CHARS)


class ActivateResponse(BaseModel):
    """The version now being served."""

    active_version: str


class EndpointHealth(BaseModel):
    """Whether a chat server is configured and answered ``GET /v1/models``."""

    configured: bool
    reachable: bool | None
    kind: str | None
    host: str | None


class EmbedderHealth(BaseModel):
    """Whether Ollama answered ``GET /api/tags``, and the model the next build uses."""

    reachable: bool | None
    model: str


class HealthResponse(BaseModel):
    """Reachability of the primary, the fallback and the embedder; never content."""

    primary: EndpointHealth
    fallback: EndpointHealth
    embedder: EmbedderHealth


class OutcomeCount(BaseModel):
    """One outcome kind's count; ``count`` is null when it is below the cohort."""

    kind: str
    count: int | None
    suppressed: bool


class StageLatency(BaseModel):
    """p50 and p95 seconds for one stage; null before anything was timed."""

    stage: str
    p50: float | None
    p95: float | None


class UsageResponse(BaseModel):
    """Counts and latencies since the process started, with small counts hidden."""

    since: str
    min_cohort: int
    outcomes: list[OutcomeCount]
    latency: list[StageLatency]


class ReportResponse(BaseModel):
    """The newest eval summary, when there is a usable one."""

    available: bool
    summary: dict[str, Any] | None


# ── Dependencies ─────────────────────────────────────────────────────────


_MODEL_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")


def _builder_env() -> dict[str, str]:
    """This process's environment with the live embedding model set, if it is a safe name."""
    env = os.environ.copy()
    model = priest_config.embed_model()
    if _MODEL_NAME.match(model):
        env["PRIEST_EMBED_MODEL"] = model
    else:
        env.pop("PRIEST_EMBED_MODEL", None)
    return env


def spawn_builder() -> int:
    """Start the index builder as a detached process and return its pid.

    No shell and no arguments from a request; output is discarded because the builder
    reports through its status file; the environment is a copy of this process's, plus
    the embedding model the dashboard shows (the builder has no dashboard settings).
    """
    process = subprocess.Popen(
        [sys.executable, "-m", _BUILDER_MODULE],
        cwd=_BACKEND_DIR,
        env=_builder_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    # Reap it when it exits so a finished builder does not linger as a zombie.
    threading.Thread(target=process.wait, daemon=True).start()
    return process.pid


def get_builder_launcher() -> BuilderLauncher:
    """The launcher the reindex route uses; tests replace it so nothing is spawned."""
    return spawn_builder


def build_probe_client(
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """A client for health probes: short timeout, no redirects, no environment proxy."""
    return httpx.AsyncClient(
        timeout=PROBE_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    )


async def get_probe_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield the probe client for one request."""
    async with build_probe_client() as client:
        yield client


def _index_dir() -> Path:
    return Path(settings.PRIEST_INDEX_DIR)


# ── GET /index ───────────────────────────────────────────────────────────


def _summary(manifest: IndexManifest) -> ManifestSummary:
    """Pick the showable fields by name, so a new manifest field is never leaked."""
    return ManifestSummary(
        version=manifest.version,
        created_at=manifest.created_at,
        embed_model=manifest.embed_model,
        embed_digest=manifest.embed_digest,
        dim=manifest.dim,
        chunk_count=manifest.chunk_count,
        note_count=manifest.note_count,
        exclusions=dict(manifest.exclusions),
        unresolved_links=manifest.unresolved_links,
        warnings=list(manifest.warnings),
        build_seconds=manifest.build_seconds,
        cleaner_version=manifest.cleaner_version,
        chunker_version=manifest.chunker_version,
    )


def _active_manifest(root: Path) -> IndexManifest | None:
    """The active version's manifest, or ``None`` if there is none or it is unusable."""
    # The loader the Guide itself uses, so this view adds no second copy of the index.
    try:
        return index_store.shared_active_index(root).get().manifest
    except PriestIndexError:
        return None


def _index_overview(root: Path) -> IndexResponse:
    manifest = _active_manifest(root)
    return IndexResponse(
        active_version=manifest.version if manifest else None,
        manifest=_summary(manifest) if manifest else None,
        versions=index_store.list_versions(root),
        configured_embed_model=priest_config.embed_model(),
    )


@router.get("/index", response_model=IndexResponse, summary="Active index summary")
async def get_index() -> IndexResponse:
    """Return the active version, its manifest summary and the retained versions."""
    return await asyncio.to_thread(_index_overview, _index_dir())


# ── POST /reindex and GET /reindex/status ────────────────────────────────


def _build_in_progress(root: Path) -> bool:
    """Whether a live builder is running, by its status file or by holding the lock.

    The lock is probed by taking it: a live holder refuses, and a stale one is taken
    over. A lock taken here is released at once, so this leaves nothing behind.
    """
    if build_status.read(root).state is BuildState.running:
        return True
    pid = os.getpid()
    if not build_status.acquire_lock(root, pid=pid):
        return True
    build_status.release_lock(root, pid=pid)
    return False


def _launch(launch: BuilderLauncher, root: Path, started_at: str) -> None:
    """Start the builder and record it as running at once.

    The builder takes a moment to write its own status; until it does, a second
    request would find nothing running. Writing the pid here closes that window.
    """
    try:
        pid = launch()
    except OSError as exc:
        logger.error("could not start the index builder: %s", type(exc).__name__)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="builder_launch_failed"
        ) from exc
    with contextlib.suppress(OSError):
        build_status.write(
            root,
            BuildStatus(state=BuildState.running, started_at=started_at, pid=pid),
        )


@router.post(
    "/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReindexResponse,
    summary="Start an index build",
)
async def start_reindex(
    request: Request,
    launch: BuilderLauncher = Depends(get_builder_launcher),
    actor: User | None = Depends(require_admin_access),
    session: AsyncSession = session_dependency,
) -> ReindexResponse:
    """Start the builder in the background, or 409 if one is already running."""
    root = _index_dir()
    if _build_in_progress(root):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="already_running")
    started_at = datetime.now(timezone.utc).isoformat()
    _launch(launch, root, started_at)
    response = ReindexResponse(state="running", started_at=started_at)
    # Last database call: it commits, so nothing after it may fail.
    await audit_service.record(
        session,
        actor=actor,
        actor_label=None if actor else audit_service.ADMIN_KEY_ACTOR_LABEL,
        action=AuditAction.priest_reindex,
        source_ip=audit_service.client_ip(request),
    )
    return response


@router.get(
    "/reindex/status",
    response_model=BuildStatusResponse,
    summary="State of the current or last index build",
)
async def get_reindex_status() -> dict[str, Any]:
    """Return the build status: state, timestamps, counts and an error code."""
    return build_status.read(_index_dir()).to_json()


# ── POST /activate ───────────────────────────────────────────────────────


@router.post(
    "/activate",
    response_model=ActivateResponse,
    summary="Serve a retained index version (rollback)",
)
async def activate_version(
    body: ActivateRequest,
    request: Request,
    actor: User | None = Depends(require_admin_access),
    session: AsyncSession = session_dependency,
) -> ActivateResponse:
    """Point the active index at an existing version that loads cleanly."""
    root = _index_dir()
    if body.version not in await asyncio.to_thread(index_store.list_versions, root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version_not_found")
    try:
        await asyncio.to_thread(index_store.activate_exclusive, root, body.version)
    except index_store.IndexBusyError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="build_in_progress"
        ) from exc
    except PriestIndexError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="version_not_loadable"
        ) from exc
    logger.info("priest index %s activated", body.version)
    response = ActivateResponse(active_version=body.version)
    # Last database call: it commits, so nothing after it may fail.
    await audit_service.record(
        session,
        actor=actor,
        actor_label=None if actor else audit_service.ADMIN_KEY_ACTOR_LABEL,
        action=AuditAction.priest_activate,
        source_ip=audit_service.client_ip(request),
        detail=body.version,
    )
    return response


# ── GET /health ──────────────────────────────────────────────────────────

_UNCONFIGURED = EndpointHealth(configured=False, reachable=None, kind=None, host=None)


async def _probe(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> bool:
    """Whether ``GET url`` answers 2xx in time. No body is sent or read."""

    async def fetch() -> bool:
        async with client.stream("GET", url, headers=headers) as response:
            return response.is_success

    try:
        return await asyncio.wait_for(fetch(), PROBE_TIMEOUT_SECONDS)
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError):
        return False


def _resolved(resolve: Callable[[], ChatEndpoint | None]) -> ChatEndpoint | None:
    """The endpoint, or ``None`` when unset or refused; the reason is not reported."""
    try:
        return resolve()
    except PriestEndpointError:
        return None


def _probe_headers(endpoint: ChatEndpoint) -> dict[str, str]:
    """The key goes to the vLLM server only, never to Ollama."""
    if endpoint.kind != "vllm" or not endpoint.api_key:
        return {}
    return {"Authorization": f"Bearer {endpoint.api_key}"}


async def _chat_health(
    client: httpx.AsyncClient, endpoint: ChatEndpoint | None
) -> EndpointHealth:
    if endpoint is None:
        return _UNCONFIGURED
    reachable = await _probe(
        client, f"{endpoint.base_url}/v1/models", _probe_headers(endpoint)
    )
    return EndpointHealth(
        configured=True, reachable=reachable, kind=endpoint.kind, host=endpoint.host
    )


def _embedder_base() -> str | None:
    """The Ollama address to probe, or ``None`` if it is malformed or a hosted API."""
    # The environment's address, as the embedder and the index build use it; the live
    # (dashboard) layer would make this badge describe a server nothing talks to.
    raw = settings.OLLAMA_BASE_URL.strip()
    try:
        base, host, _ = themes_endpoint._validated_base(raw)
    except ThemesEndpointError:
        return None
    return None if chat_endpoint.is_third_party_host(host) else base


async def _embedder_health(client: httpx.AsyncClient) -> EmbedderHealth:
    base = _embedder_base()
    reachable = None if base is None else await _probe(client, f"{base}/api/tags", {})
    return EmbedderHealth(reachable=reachable, model=priest_config.embed_model())


@router.get("/health", response_model=HealthResponse, summary="Probe the model servers")
async def get_health(
    client: httpx.AsyncClient = Depends(get_probe_client),
) -> HealthResponse:
    """Report whether the chat servers and the embedder answer a models listing."""
    primary, fallback, embedder = await asyncio.gather(
        _chat_health(client, _resolved(chat_endpoint.resolve_primary)),
        _chat_health(client, _resolved(chat_endpoint.resolve_fallback)),
        _embedder_health(client),
    )
    return HealthResponse(primary=primary, fallback=fallback, embedder=embedder)


# ── GET /usage ───────────────────────────────────────────────────────────


def _suppressed(kind: str, count: int, cohort: int) -> OutcomeCount:
    """Hide a count below the cohort, zero included: zero versus hidden would show
    that a small number of something happened."""
    if count < cohort:
        return OutcomeCount(kind=kind, count=None, suppressed=True)
    return OutcomeCount(kind=kind, count=count, suppressed=False)


def _kinds(fixed: tuple[str, ...]) -> list[str]:
    """Every fixed kind, plus ``other`` always, so a zero and a hidden count look alike."""
    return [*fixed, metrics.OTHER]


@router.get("/usage", response_model=UsageResponse, summary="Usage since start")
async def get_usage() -> UsageResponse:
    """Return outcome counts (small ones hidden) and p50/p95 latency per stage."""
    snap = metrics.snapshot()
    cohort = insights_service.min_cohort()
    # A timing beside counts that are all hidden would show that one question happened.
    show_latency = sum(snap.outcomes.values()) >= cohort
    return UsageResponse(
        since=snap.since.isoformat(),
        min_cohort=cohort,
        outcomes=[
            _suppressed(kind, snap.outcomes.get(kind, 0), cohort)
            for kind in _kinds(metrics.OUTCOMES)
        ],
        latency=[
            StageLatency(
                stage=stage,
                p50=snap.latency_p50.get(stage) if show_latency else None,
                p95=snap.latency_p95.get(stage) if show_latency else None,
            )
            for stage in _kinds(metrics.STAGES)
        ],
    )


# ── GET /report ──────────────────────────────────────────────────────────

_UNAVAILABLE = ReportResponse(available=False, summary=None)


def _newest_report(directory: Path) -> Path | None:
    """The most recently modified ``*.json`` regular file directly in *directory*.

    Symlinks and sub-directories are skipped, and a symlinked *directory* is refused,
    so nothing outside the reports directory can be read through it.
    """
    if directory.is_symlink():
        return None
    try:
        with os.scandir(directory) as entries:
            found = [
                (entry.stat(follow_symlinks=False).st_mtime_ns, entry.name)
                for entry in entries
                if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False)
            ]
    except OSError:
        return None
    return directory / max(found)[1] if found else None


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _read_summary(path: Path) -> dict[str, Any] | None:
    """Parse *path* if it is a JSON object of at most ``MAX_REPORT_BYTES``."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            return None
        parsed = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (OSError, ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _latest_report(root: Path) -> ReportResponse:
    path = _newest_report(root / _REPORTS_DIR)
    summary = _read_summary(path) if path else None
    if summary is None:
        return _UNAVAILABLE
    return ReportResponse(available=True, summary=summary)


@router.get("/report", response_model=ReportResponse, summary="Latest eval summary")
async def get_report() -> ReportResponse:
    """Return the newest eval summary the scripts wrote, if it is a small JSON object."""
    return await asyncio.to_thread(_latest_report, _index_dir())
