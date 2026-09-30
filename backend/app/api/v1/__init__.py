"""API v1 router — mounts all versioned endpoint modules."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.audit import router as audit_router
from app.api.v1.auth import router as auth_router
from app.api.v1.confessions import router as confessions_router
from app.api.v1.delivery import router as delivery_router
from app.api.v1.departments import router as departments_router
from app.api.v1.health import router as health_router
from app.api.v1.hr import router as hr_router
from app.api.v1.hr_themes import router as hr_themes_router
from app.api.v1.moderation import router as moderation_router
from app.api.v1.priest import router as priest_router
from app.api.v1.priest_admin import router as priest_admin_router
from app.api.v1.privacy import router as privacy_router
from app.api.v1.stt import router as stt_router
from app.api.v1.tts import router as tts_router
from app.api.v1.voice import router as voice_router

router = APIRouter(prefix="/api/v1")

router.include_router(health_router)
router.include_router(auth_router)
router.include_router(admin_router)
router.include_router(audit_router)
router.include_router(confessions_router)
router.include_router(hr_router)
router.include_router(hr_themes_router)
router.include_router(privacy_router)
router.include_router(priest_router)
router.include_router(priest_admin_router)
router.include_router(departments_router)
router.include_router(moderation_router)
router.include_router(delivery_router)
router.include_router(stt_router)
router.include_router(tts_router)
router.include_router(voice_router)
