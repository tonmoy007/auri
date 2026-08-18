"""Admin dashboard API — live config, ngrok tunnel status, LiveKit status.

Phase 10, local dev only. Every endpoint requires the ``X-Admin-Api-Key``
header to match ``settings.ADMIN_API_KEY`` (mirrors ``require_moderator``
in moderation.py).
"""

from __future__ import annotations

import json
import logging

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
_VOICE_MASK_KEYS: tuple[str, ...] = tuple(f"VOICE_MASK_{name.upper()}" for name in MASKS)
ALLOWED_CONFIG_KEYS = frozenset(_LLM_KEYS + _STT_KEYS + _VOICE_MASK_KEYS)

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
        t["public_url"] for t in tunnels if t.get("public_url", "").startswith("https://")
    ]
    if not https_urls:
        return NgrokStatus(running=False)
    return NgrokStatus(running=True, public_url=https_urls[0])


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
