"""FastAPI application factory for the Auri confession booth backend."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.v1 import router as api_v1_router
from app.config import parse_comma_separated_list, settings
from app.database import async_session_factory, engine
from app.exceptions import (
    AuthConfigurationError,
    PriestUnavailableError,
    RateLimitError,
)
from app.observability import init_sentry, mount_metrics
from app.priest.rate_limiter import PriestRateLimitError
from app.services.department_service import seed_from_env_if_empty
from app.services.settings_service import load_cache as load_config_cache
from app.services.user_service import bootstrap_admin

# ── Logging initialisation ───────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


class RootHealthResponse(BaseModel):
    """Response body for the root-level liveness probe."""

    status: str


# ── Lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup/shutdown lifecycle handler.

    On **startup**: in ``development`` only, create database tables if they
    don't exist (idempotent convenience for local work). Staging and
    production schemas are managed exclusively via ``alembic upgrade head``
    (AGENTS.md §7.4) — this block does not run there.
    On **shutdown**: dispose the database connection pool.
    """
    logger.info("Starting Auri backend", environment=settings.ENVIRONMENT)

    if settings.ENVIRONMENT == "development":
        from app.models.base import Base

        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables ensured (development auto-create)")
        except Exception as exc:  # noqa: BLE001 — dev-only auto-create table boundary; DB not being ready yet shouldn't crash app startup
            logger.warning(
                "Could not create database tables (DB may not be ready)", error=str(exc)
            )
    else:
        logger.info(
            "Skipping auto-create; run 'alembic upgrade head' to apply migrations"
        )

    try:
        async with async_session_factory() as session:
            await load_config_cache(session)
    except Exception as exc:  # noqa: BLE001 — dashboard config cache is an optional live-override layer; failing to load it should fall back to Settings()/.env, not crash startup
        logger.warning(
            "Could not load live config overrides (DB may not be ready)", error=str(exc)
        )

    try:
        async with async_session_factory() as session:
            await seed_from_env_if_empty(session)
    except Exception as exc:  # noqa: BLE001 — seeding the department directory is a first-run convenience; a failure here (DB not ready) must not take the API down, and it retries on the next start
        logger.warning("Could not seed the department directory", error=str(exc))

    try:
        async with async_session_factory() as session:
            await bootstrap_admin(
                session,
                settings.ADMIN_BOOTSTRAP_EMAIL,
                settings.ADMIN_BOOTSTRAP_PASSWORD,
            )
    except Exception as exc:  # noqa: BLE001 — bootstrapping the first staff account is a convenience for a fresh deployment; a failure here (DB not ready, malformed env credentials) must not take the API down, and it retries on the next start
        logger.warning("Could not bootstrap the first admin", error=str(exc))

    yield

    logger.info("Shutting down Auri backend")
    await engine.dispose()


# ── Application factory ──────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Build and return a fully-configured FastAPI application instance."""
    init_sentry(settings.SENTRY_DSN, settings.ENVIRONMENT)

    app = FastAPI(
        title="Auri — Anonymous Confession Booth API",
        description=(
            "Auri is an anonymous AI-powered confession booth. "
            "This API provides endpoints for submitting, managing, and "
            "forwarding anonymous confessions with STT, TTS, voice modulation, "
            "and LLM-based de-identification."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────
    # Never wildcard origins while allow_credentials=True — that combination
    # is rejected by browsers and is a CSRF risk. Origins come from settings.
    origins = parse_comma_separated_list(settings.CORS_ORIGINS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception handlers ───────────────────────────────────────────────

    @app.exception_handler(RateLimitError)
    async def rate_limit_error_handler(
        request: Request, exc: RateLimitError
    ) -> JSONResponse:
        """Map a domain ``RateLimitError`` to an HTTP 429 response."""
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.exception_handler(PriestRateLimitError)
    async def priest_rate_limit_handler(
        request: Request, exc: PriestRateLimitError
    ) -> JSONResponse:
        """Map a guide rate-limit refusal to a 429 with whole-second ``Retry-After``.

        Registered alongside the generic ``RateLimitError`` handler; the subclass
        wins, so confession limits keep their own message and no header.
        """
        return JSONResponse(
            status_code=429,
            content={"detail": "rate_limited"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @app.exception_handler(PriestUnavailableError)
    async def priest_unavailable_handler(
        request: Request, exc: PriestUnavailableError
    ) -> JSONResponse:
        """Map "the guide cannot serve this now" to a 503 carrying only its code."""
        headers = (
            {} if exc.retry_after is None else {"Retry-After": str(exc.retry_after)}
        )
        return JSONResponse(
            status_code=503, content={"detail": exc.code}, headers=headers
        )

    @app.exception_handler(AuthConfigurationError)
    async def auth_configuration_error_handler(
        request: Request, exc: AuthConfigurationError
    ) -> JSONResponse:
        """Map a missing session secret to a 503, not a generic 500.

        Sessions cannot be issued at all in this state — that is deliberate
        (see app/services/auth_tokens.py), and the operator needs to see
        *why* rather than a stack trace.
        """
        logger.error("staff sessions unavailable", error=str(exc))
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    # ── Routers ───────────────────────────────────────────────────────────
    app.include_router(api_v1_router)

    # ── Observability ────────────────────────────────────────────────────
    mount_metrics(app)

    # Health endpoint at root level.
    @app.get("/health", response_model=RootHealthResponse)
    async def health() -> RootHealthResponse:
        """Simple liveness probe (detailed check under ``/api/v1/health``)."""
        return RootHealthResponse(status="ok")

    # ── WebSocket: live confession stream ─────────────────────────────────

    @app.websocket("/ws/confession")
    async def confession_websocket(websocket: WebSocket) -> None:
        """WebSocket endpoint for real-time confession streaming.

        Accepts a connection and echoes back received messages prefixed
        with a server acknowledgement.  Designed for future integration
        with streaming STT and TTS.
        """
        await websocket.accept()
        logger.info("WebSocket client connected", client=websocket.client)

        try:
            while True:
                data = await websocket.receive_text()
                logger.debug("WebSocket received", data_len=len(data))
                await websocket.send_json(
                    {
                        "type": "ack",
                        "received_length": len(data),
                        "message": "Confession data received. Processing…",
                    }
                )
        except WebSocketDisconnect:
            logger.info("WebSocket client disconnected")

    return app


# ── Entrypoint (``uvicorn app.main:app``) ────────────────────────────────

app = create_app()
