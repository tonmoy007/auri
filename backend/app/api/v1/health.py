"""Health-check endpoint for monitoring and load-balancer probes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import settings
from app.database import check_db_connected

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(
    db_ok: bool = Depends(check_db_connected),
) -> dict:
    """Return service status, database connectivity and current version.

    This endpoint is intentionally unauthenticated so that orchestrators
    (Kubernetes, Docker Compose healthchecks, etc.) can poll it freely.
    """
    return {
        "status": "healthy" if db_ok else "degraded",
        "db_connected": db_ok,
        "version": "0.1.0",
        "environment": settings.ENVIRONMENT,
    }
