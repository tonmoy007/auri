"""API v1 router — mounts all versioned endpoint modules."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.confessions import router as confessions_router
from app.api.v1.health import router as health_router

router = APIRouter(prefix="/api/v1")

router.include_router(health_router)
router.include_router(confessions_router)
