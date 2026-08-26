"""Admin dashboard API — live config, ngrok tunnel status, LiveKit status.

Phase 10, local dev only. Every endpoint requires the ``X-Admin-Api-Key``
header to match ``settings.ADMIN_API_KEY`` (mirrors ``require_moderator``
in moderation.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_async_session
from app.services import settings_service
from app.services.voice_mod import MASKS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Keys the dashboard may read/write — a live-config surface, not an
# arbitrary DB key/value store.
_LLM_KEYS: tuple[str, ...] = (
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)
_STT_KEYS: tuple[str, ...] = ("WHISPER_MODEL",)
_VOICE_MASK_KEYS: tuple[str, ...] = tuple(
    f"VOICE_MASK_{name.upper()}" for name in MASKS
)
# ANDROID_JAVA_HOME: optional JAVA_HOME override for the "Build APK" action
# (task 10.6) — Android Gradle Plugin requires Java 17, which isn't every
# machine's default `java`. Empty means "inherit the backend process's own
# JAVA_HOME".
_BUILD_KEYS: tuple[str, ...] = ("ANDROID_JAVA_HOME",)
ALLOWED_CONFIG_KEYS = frozenset(_LLM_KEYS + _STT_KEYS + _VOICE_MASK_KEYS + _BUILD_KEYS)

# repo_root/backend/app/api/v1/admin.py -> repo_root/mobile
_MOBILE_DIR = Path(__file__).resolve().parents[4] / "mobile"
_APK_PATH = (
    _MOBILE_DIR
    / "android"
    / "app"
    / "build"
    / "outputs"
    / "apk"
    / "release"
    / "app-release.apk"
)
# Gradle's createBundleReleaseJsAndAssets task doesn't declare .env.local as
# an input, so it happily marks itself UP-TO-DATE and reuses a stale JS
# bundle (with a stale baked-in EXPO_PUBLIC_API_URL) when nothing else
# changed — confirmed empirically by extracting a "successful" build's APK
# and finding a URL from days earlier. Deleting these output dirs before
# each build forces the task to regenerate them regardless of Gradle's own
# staleness check.
_STALE_BUNDLE_DIRS: tuple[Path, ...] = (
    _MOBILE_DIR
    / "android"
    / "app"
    / "build"
    / "generated"
    / "assets"
    / "createBundleReleaseJsAndAssets",
    _MOBILE_DIR
    / "android"
    / "app"
    / "build"
    / "generated"
    / "res"
    / "createBundleReleaseJsAndAssets",
)
_MAX_LOG_LINES = 2000

_SECRET_SUFFIXES = ("_API_KEY", "_API_SECRET")


def require_admin(x_admin_api_key: str = Header(..., alias="X-Admin-Api-Key")) -> None:
    """Reject the request unless it carries the configured admin secret.

    Fails **closed**: an unset ``ADMIN_API_KEY`` denies every request rather
    than leaving the dashboard open (mirrors ``require_moderator``).
    """
    if not settings.ADMIN_API_KEY or x_admin_api_key != settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing admin credentials",
        )


def _mask(key: str, value: str) -> str:
    """Mask secret values for display — show only the last 4 characters."""
    if not value or not any(key.endswith(s) for s in _SECRET_SUFFIXES):
        return value
    return f"***{value[-4:]}" if len(value) > 4 else "****"


def _default_for(key: str) -> str:
    """Return *key*'s built-in default — a ``Settings()`` field, or for a
    ``VOICE_MASK_*`` key, its JSON-encoded built-in SoX effect chain."""
    if key.startswith("VOICE_MASK_"):
        mask_name = key.removeprefix("VOICE_MASK_").lower()
        return json.dumps(MASKS.get(mask_name, MASKS["warm"]))
    return str(getattr(settings, key, ""))


class ConfigEntry(BaseModel):
    """A single dashboard-configurable setting."""

    key: str
    value: str
    source: str  # "db" (dashboard override) or "default" (.env/Settings())


class ConfigResponse(BaseModel):
    """All dashboard-configurable settings, grouped by category."""

    llm: list[ConfigEntry]
    stt: list[ConfigEntry]
    voice_masks: list[ConfigEntry]
    build: list[ConfigEntry]


class ConfigUpdateRequest(BaseModel):
    """Body for ``PUT /admin/config`` — one key/value override."""

    key: str
    value: str


class NgrokStatus(BaseModel):
    """Current ngrok tunnel state, read from ngrok's local API."""

    running: bool
    public_url: str | None = None


class LiveKitStatus(BaseModel):
    """Reachability of the configured (self-hosted) LiveKit server."""

    reachable: bool
    url: str


def _entries(keys: tuple[str, ...]) -> list[ConfigEntry]:
    return [
        ConfigEntry(
            key=key,
            value=_mask(key, settings_service.get_config(key, _default_for(key))),
            source="db" if settings_service.is_overridden(key) else "default",
        )
        for key in keys
    ]


@router.get(
    "/config",
    response_model=ConfigResponse,
    dependencies=[Depends(require_admin)],
    summary="List every dashboard-configurable setting and its live value",
)
async def get_config_all() -> ConfigResponse:
    """Return LLM/STT/voice-mask config — secret values masked to their last 4 chars."""
    return ConfigResponse(
        llm=_entries(_LLM_KEYS),
        stt=_entries(_STT_KEYS),
        voice_masks=_entries(_VOICE_MASK_KEYS),
        build=_entries(_BUILD_KEYS),
    )


@router.put(
    "/config",
    response_model=ConfigEntry,
    dependencies=[Depends(require_admin)],
    summary="Set a config override — takes effect immediately, no restart",
)
async def update_config(
    body: ConfigUpdateRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ConfigEntry:
    """Upsert *body.key* = *body.value*. Rejects unknown keys and, for
    ``VOICE_MASK_*`` keys, anything that isn't a JSON list of strings."""
    if body.key not in ALLOWED_CONFIG_KEYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown config key: {body.key!r}",
        )
    if body.key.startswith("VOICE_MASK_"):
        try:
            parsed = json.loads(body.value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{body.key} value must be JSON: {exc}",
            ) from exc
        if not isinstance(parsed, list) or not all(isinstance(a, str) for a in parsed):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{body.key} value must be a JSON list of strings",
            )

    await settings_service.set_config(session, body.key, body.value)
    return ConfigEntry(key=body.key, value=_mask(body.key, body.value), source="db")


@router.delete(
    "/config/{key}",
    dependencies=[Depends(require_admin)],
    summary="Clear a config override, reverting it to the .env/Settings() default",
)
async def reset_config(
    key: str,
    session: AsyncSession = Depends(get_async_session),
) -> ConfigEntry:
    """Remove *key*'s DB override."""
    if key not in ALLOWED_CONFIG_KEYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown config key: {key!r}",
        )
    await settings_service.clear_config(session, key)
    return ConfigEntry(key=key, value=_mask(key, _default_for(key)), source="default")


@router.get(
    "/ngrok",
    response_model=NgrokStatus,
    dependencies=[Depends(require_admin)],
    summary="Current ngrok tunnel public URL, if ngrok is running locally",
)
async def get_ngrok_status() -> NgrokStatus:
    """Read ngrok's local API (``localhost:4040``) — a local hop, not a network call."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://localhost:4040/api/tunnels")
            resp.raise_for_status()
            tunnels = resp.json().get("tunnels", [])
    except Exception:  # noqa: BLE001 — deliberate fail-safe boundary: ngrok not running is the common case, not an error
        return NgrokStatus(running=False)

    https_urls = [
        t["public_url"]
        for t in tunnels
        if t.get("public_url", "").startswith("https://")
    ]
    if not https_urls:
        return NgrokStatus(running=False)
    return NgrokStatus(running=True, public_url=https_urls[0])


class BuildRequest(BaseModel):
    """Body for ``POST /admin/build-apk`` — the URL to bake into the APK."""

    backend_url: str


class BuildStatus(BaseModel):
    """Current state of the (at most one, at a time) local APK build."""

    status: str  # "idle" | "running" | "success" | "failed"
    log: str
    apk_path: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


class _BuildState:
    """Module-level build state — single-operator local dev tool, so one
    build at a time is a deliberate simplification, not a missing feature."""

    def __init__(self) -> None:
        self.status = "idle"
        self.log: list[str] = []
        self.apk_path: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None

    def append(self, line: str) -> None:
        self.log.append(line)
        if len(self.log) > _MAX_LOG_LINES:
            self.log = self.log[-_MAX_LOG_LINES:]

    def snapshot(self) -> BuildStatus:
        return BuildStatus(
            status=self.status,
            log="\n".join(self.log),
            apk_path=self.apk_path,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


_build_state = _BuildState()


def _derive_ws_url(base_url: str) -> str:
    """Mirror mobile's ``config/api.ts`` ``deriveWsUrl`` (http→ws, https→wss)."""
    return re.sub(r"^http", "ws", base_url) + "/ws/confession"


async def _run_build(backend_url: str, java_home: str) -> None:
    """Run the build, guaranteeing `_build_state` never gets stuck at "running".

    This coroutine is launched via ``asyncio.create_task`` — fire-and-forget,
    no caller ever awaits it — so an unhandled exception here doesn't
    propagate anywhere; it just vanishes into asyncio's "Task exception was
    never retrieved" log and leaves the dashboard polling a "running" status
    forever. Real bug, caught live: running the backend from a container
    without `mobile/` on disk hit exactly this (`_run_build_steps`'s first
    line, `.write_text()` on a nonexistent directory) and hung silently.
    """
    try:
        await _run_build_steps(backend_url, java_home)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget task boundary; must never leave _build_state stuck at "running" (see docstring)
        _build_state.append(f"Build crashed: {exc}")
        _build_state.status = "failed"
        _build_state.finished_at = time.time()


async def _run_build_steps(backend_url: str, java_home: str) -> None:
    """Write mobile/.env.local, run gradlew assembleRelease, stream output into `_build_state`.

    Release, not debug: the debug build type relies on a live Metro
    connection and never embeds a JS bundle at all (confirmed empirically —
    an assembleDebug APK's assets/ has no bundle file), which would silently
    defeat the entire point of this action. Release embeds the bundle via
    Expo's `export:embed`, where EXPO_PUBLIC_API_URL actually gets inlined.
    The debug keystore signs it (see android/app/build.gradle — no real
    release keystore is configured yet), so it installs without extra setup.

    Caller (`start_build`) has already flipped `_build_state.status` to
    "running" synchronously — this coroutine doesn't run until the event
    loop next yields, so doing it here instead would race a concurrent
    request's "is a build already running?" check.
    """
    env_local = _MOBILE_DIR / ".env.local"
    env_local.write_text(
        f"EXPO_PUBLIC_API_URL={backend_url}\n"
        f"EXPO_PUBLIC_WS_URL={_derive_ws_url(backend_url)}\n"
    )
    _build_state.append(f"Wrote {env_local} for this build.")

    for stale_dir in _STALE_BUNDLE_DIRS:
        shutil.rmtree(stale_dir, ignore_errors=True)
    _APK_PATH.unlink(missing_ok=True)
    _build_state.append(
        "Cleared cached JS bundle output — forcing a fresh embed this build."
    )

    env = {**os.environ}
    if java_home:
        env["JAVA_HOME"] = java_home

    gradlew = _MOBILE_DIR / "android" / "gradlew"
    try:
        proc = await asyncio.create_subprocess_exec(
            str(gradlew),
            "assembleRelease",
            "--console=plain",
            cwd=str(_MOBILE_DIR / "android"),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        _build_state.append(f"Failed to start gradlew: {exc}")
        _build_state.status = "failed"
        _build_state.finished_at = time.time()
        return

    assert proc.stdout is not None  # PIPE was requested above, stdout is always set
    async for raw_line in proc.stdout:
        _build_state.append(raw_line.decode(errors="replace").rstrip())

    returncode = await proc.wait()
    _build_state.finished_at = time.time()
    if returncode == 0 and _APK_PATH.exists():
        _build_state.status = "success"
        _build_state.apk_path = str(_APK_PATH)
        _build_state.append(f"Build succeeded: {_APK_PATH}")
    else:
        _build_state.status = "failed"
        _build_state.append(f"gradlew exited {returncode}")


@router.post(
    "/build-apk",
    response_model=BuildStatus,
    dependencies=[Depends(require_admin)],
    summary="Build a standalone APK locally with the given backend URL baked in",
)
async def start_build(body: BuildRequest) -> BuildStatus:
    """Kick off ``gradlew assembleRelease`` in the background; poll `GET
    /admin/build-apk/status` for progress. Rejects a second build while one
    is already running (409) — this is a single-operator local dev tool."""
    if not re.match(r"^https?://.+", body.backend_url):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="backend_url must be a valid http:// or https:// URL",
        )
    if _build_state.status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A build is already running",
        )

    _build_state.status = "running"
    _build_state.log = []
    _build_state.apk_path = None
    _build_state.started_at = time.time()
    _build_state.finished_at = None

    java_home = settings_service.get_config("ANDROID_JAVA_HOME", "")
    asyncio.create_task(_run_build(body.backend_url.rstrip("/"), java_home))
    return _build_state.snapshot()


@router.get(
    "/build-apk/status",
    response_model=BuildStatus,
    dependencies=[Depends(require_admin)],
    summary="Poll the current (or most recent) local APK build's status and log",
)
async def get_build_status() -> BuildStatus:
    """Return the in-memory build state — resets when the backend restarts."""
    return _build_state.snapshot()


@router.get(
    "/livekit",
    response_model=LiveKitStatus,
    dependencies=[Depends(require_admin)],
    summary="Reachability of the self-hosted LiveKit server",
)
async def get_livekit_status() -> LiveKitStatus:
    """Probe the configured ``LIVEKIT_URL``'s signal port for reachability."""
    ws_url = settings_service.get_config("LIVEKIT_URL", settings.LIVEKIT_URL)
    http_url = ws_url.replace("wss://", "https://").replace("ws://", "http://")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(http_url)
            reachable = resp.status_code == status.HTTP_200_OK
    except Exception:  # noqa: BLE001 — deliberate fail-safe boundary: LiveKit not running is expected until 10.1's docker service is started
        reachable = False
    return LiveKitStatus(reachable=reachable, url=ws_url)
