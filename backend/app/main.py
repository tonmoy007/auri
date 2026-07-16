"""FastAPI application factory for the Auri confession booth backend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import router as api_v1_router
from app.config import settings
from app.database import engine

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


# ── Lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup/shutdown lifecycle handler.

    On **startup**: create database tables if they don't exist (idempotent).
    On **shutdown**: dispose the database connection pool.
    """
    logger.info("Starting Auri backend", environment=settings.ENVIRONMENT)

    # Create tables (idempotent — uses SQLAlchemy's ``CREATE TABLE IF NOT EXISTS``
    # behaviour when called via ``Base.metadata.create_all``).
    from app.models.base import Base

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables ensured")
    except Exception as exc:
        logger.warning("Could not create database tables (DB may not be ready)", error=str(exc))

    yield

    logger.info("Shutting down Auri backend")
    await engine.dispose()


# ── Application factory ──────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Build and return a fully-configured FastAPI application instance."""
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten in production.
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────
    app.include_router(api_v1_router)

    # Health endpoint at root level.
    @app.get("/health")
    async def health() -> dict:
        """Simple liveness probe (detailed check under ``/api/v1/health``)."""
        return {"status": "ok"}

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
                await websocket.send_json({
                    "type": "ack",
                    "received_length": len(data),
                    "message": "Confession data received. Processing…",
                })
        except WebSocketDisconnect:
            logger.info("WebSocket client disconnected")

    return app


# ── Entrypoint (``uvicorn app.main:app``) ────────────────────────────────

app = create_app()
