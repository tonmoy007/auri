"""CRUD endpoints for anonymous confessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.confession import Confession, ConfessionStatus
from app.models.user import AnonymousUser

router = APIRouter(prefix="/confessions", tags=["confessions"])


# ── Pydantic request/response schemas ────────────────────────────────────


class ConfessionCreate(BaseModel):
    """Request body for creating a new confession."""

    device_token_hash: str = Field(
        ..., min_length=16, max_length=256, description="SHA-256 hash of device token"
    )
    voice_mask: str = Field(
        "warm",
        pattern=r"^(warm|robotic|ethereal|deep|random)$",
        description="Voice modulation mask identifier",
    )
    transcript: str = Field(
        ..., min_length=1, description="Whisper-generated transcript text"
    )


class ConfessionResponse(BaseModel):
    """Public response representation of a confession."""

    id: uuid.UUID
    voice_mask: str
    transcript: str
    ai_summary: str | None
    category: str | None
    pii_stripped: bool
    status: ConfessionStatus
    recipient_dept: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConfessionForward(BaseModel):
    """Request body for forwarding a confession to a department."""

    department: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Target recipient department",
    )


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=ConfessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new anonymous confession",
)
async def create_confession(
    body: ConfessionCreate,
    session: AsyncSession = Depends(get_async_session),
) -> Confession:
    """Persist a new confession and upsert the anonymous user record.

    The ``device_token_hash`` is used to track usage frequency without
    identifying the user.  Future steps will run Whisper STT, de-identify
    the transcript via LLM, and assign a category.
    """
    confession = Confession(
        device_token_hash=body.device_token_hash,
        voice_mask=body.voice_mask,
        transcript=body.transcript,
    )
    session.add(confession)

    # Upsert anonymous user stats.
    stmt = select(AnonymousUser).where(
        AnonymousUser.device_token_hash == body.device_token_hash
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        user = AnonymousUser(
            device_token_hash=body.device_token_hash,
            last_confession_at=datetime.now(timezone.utc),
            confession_count=1,
        )
        session.add(user)
    else:
        user.last_confession_at = datetime.now(timezone.utc)
        user.confession_count += 1

    await session.flush()
    await session.refresh(confession)
    return confession


@router.get(
    "/{confession_id}",
    response_model=ConfessionResponse,
    summary="Retrieve a confession by its UUID",
)
async def get_confession(
    confession_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> Confession:
    """Return a single confession identified by ``confession_id``."""
    stmt = select(Confession).where(Confession.id == confession_id)
    result = await session.execute(stmt)
    confession = result.scalar_one_or_none()

    if confession is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Confession not found",
        )
    return confession


@router.delete(
    "/{confession_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a confession",
)
async def delete_confession(
    confession_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Mark a confession as ``deleted`` without removing the row.

    The original transcript is retained for audit purposes but will not
    appear in any forward-facing query.
    """
    stmt = select(Confession).where(Confession.id == confession_id)
    result = await session.execute(stmt)
    confession = result.scalar_one_or_none()

    if confession is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Confession not found",
        )

    confession.status = ConfessionStatus.deleted


@router.post(
    "/{confession_id}/forward",
    response_model=ConfessionResponse,
    summary="Forward a confession to a recipient department",
)
async def forward_confession(
    confession_id: uuid.UUID,
    body: ConfessionForward,
    session: AsyncSession = Depends(get_async_session),
) -> Confession:
    """Change a confession's status to ``forwarded`` and assign a department.

    The confession must currently be in ``pending`` status; otherwise the
    request is rejected with a ``409 Conflict``.
    """
    stmt = select(Confession).where(Confession.id == confession_id)
    result = await session.execute(stmt)
    confession = result.scalar_one_or_none()

    if confession is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Confession not found",
        )

    if confession.status != ConfessionStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot forward confession in status '{confession.status.value}'",
        )

    confession.status = ConfessionStatus.forwarded
    confession.recipient_dept = body.department

    await session.flush()
    await session.refresh(confession)
    return confession
