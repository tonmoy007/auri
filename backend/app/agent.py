"""LiveKit Agents worker — wires the self-hosted LiveKit server (Phase 7 / task 10.7).

Standalone process, run separately from the FastAPI app (``python -m
app.agent``), mirroring ``bot/`` as its own deployment unit. Uses the
``AgentServer`` + ``@server.rtc_session`` pattern verified against
docs.livekit.io in task 7.1 (supersedes the older ``WorkerOptions``
pattern from training-data memory).

Scope is connectivity only: resolve ``LIVEKIT_URL``/``LIVEKIT_API_KEY``/
``LIVEKIT_API_SECRET`` (10.1/10.2's DB-first live-config layer, same as
``admin.py``'s LiveKit status probe) and prove the worker can join a room
on the self-hosted server. The full STT/LLM/TTS booth conversation is
task 7.3, not implemented here.

Credentials are wired via ``AgentServer.update_options(ws_url=...,
api_key=..., api_secret=...)`` — the same call the SDK's own CLI makes
for ``--url``/``--api-key``/``--api-secret`` flags — rather than left to
its ``LIVEKIT_URL``/``LIVEKIT_API_KEY``/``LIVEKIT_API_SECRET`` env-var
defaults (verified against the installed ``livekit-agents`` 1.7.0
source, since this API has moved since 7.1's 1.6.10-era notes).
Resolution only runs when the worker actually starts (``__main__``
guard), not at import time, so importing this module never makes a
network call.
"""

from __future__ import annotations

import asyncio
import logging

from livekit import agents
from livekit.agents import AgentServer, JobContext

from app.config import settings
from app.database import async_session_factory
from app.services.settings_service import get_config, load_cache

logger = logging.getLogger(__name__)

server = AgentServer()


async def _resolve_livekit_credentials() -> tuple[str, str, str]:
    """Resolve DB-first LiveKit credentials, falling back to ``Settings()``/``.env``.

    Falls back if the DB is unavailable, matching ``main.py``'s lifespan
    behaviour for the same settings cache — a worker that can't reach
    Postgres yet should still start with 10.1's static defaults, not crash.
    """
    try:
        async with async_session_factory() as session:
            await load_cache(session)
    except Exception as exc:  # noqa: BLE001 — DB unavailable is a fallback boundary, not a startup blocker (see main.py's lifespan for the same pattern)
        logger.warning("Could not load live config overrides (DB may not be ready): %s", exc)

    return (
        get_config("LIVEKIT_URL", settings.LIVEKIT_URL),
        get_config("LIVEKIT_API_KEY", settings.LIVEKIT_API_KEY),
        get_config("LIVEKIT_API_SECRET", settings.LIVEKIT_API_SECRET),
    )


@server.rtc_session(agent_name="auri-confession-booth")
async def confession_booth(ctx: JobContext) -> None:
    """Join the dispatched room and confirm connectivity.

    No ``AgentSession``/STT/LLM/TTS pipeline yet — task 7.3 replaces this
    body with the live booth conversation. This only proves the worker
    can authenticate against and join a room on the self-hosted server.
    """
    await ctx.connect()
    logger.info("LiveKit agent joined room %s", ctx.room.name)


if __name__ == "__main__":
    ws_url, api_key, api_secret = asyncio.run(_resolve_livekit_credentials())
    server.update_options(ws_url=ws_url, api_key=api_key, api_secret=api_secret)
    agents.cli.run_app(server)
