"""Department directory: seeding, lookup, and safe deletion.

The table is authoritative. The ``DEPARTMENTS`` env value only seeds it the
first time it is empty, so an existing directory is never overwritten by a
stale deploy variable.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import parse_comma_separated_list, settings
from app.exceptions import (
    DepartmentInUseError,
    DepartmentNotFoundError,
    DuplicateDepartmentError,
)
from app.models.confession import Confession, ConfessionStatus, content_present
from app.models.department import Department

logger = logging.getLogger(__name__)


async def seed_from_env_if_empty(session: AsyncSession) -> int:
    """Populate the directory from ``DEPARTMENTS`` when it has no rows.

    Returns:
        The number of departments created (0 if the table was not empty).
    """
    existing = await session.scalar(select(func.count()).select_from(Department))
    if existing:
        return 0

    names = parse_comma_separated_list(settings.DEPARTMENTS)
    for name in names:
        session.add(Department(name=name, is_active=True))
    await session.commit()

    if names:
        logger.info("Seeded %d department(s) from DEPARTMENTS", len(names))
    return len(names)


async def list_departments(
    session: AsyncSession, include_inactive: bool = False
) -> list[Department]:
    """Return departments by name, optionally including deactivated ones."""
    stmt = select(Department).order_by(Department.name)
    if not include_inactive:
        stmt = stmt.where(Department.is_active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_by_name(session: AsyncSession, name: str) -> Department | None:
    """Return the department called *name*, or ``None``."""
    result = await session.execute(select(Department).where(Department.name == name))
    return result.scalar_one_or_none()


async def create_department(
    session: AsyncSession, name: str, telegram_chat_id: str | None
) -> Department:
    """Add a department.

    Raises:
        DuplicateDepartmentError: If the name is already in the directory.
    """
    if await get_by_name(session, name) is not None:
        raise DuplicateDepartmentError(f"department already exists: {name}")

    department = Department(
        name=name, telegram_chat_id=telegram_chat_id or None, is_active=True
    )
    session.add(department)
    await session.flush()
    return department


async def update_department(
    session: AsyncSession,
    name: str,
    telegram_chat_id: str | None,
    is_active: bool,
) -> Department:
    """Update a department's delivery target and active flag.

    Raises:
        DepartmentNotFoundError: If no department is called *name*.
    """
    department = await get_by_name(session, name)
    if department is None:
        raise DepartmentNotFoundError(f"no department called {name}")

    department.telegram_chat_id = telegram_chat_id or None
    department.is_active = is_active
    await session.flush()
    return department


async def undelivered_count(session: AsyncSession, name: str) -> int:
    """Return how many confessions are still queued for *name*."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(Confession)
            .where(
                Confession.recipient_dept == name,
                Confession.status == ConfessionStatus.forwarded,
                content_present(),
                Confession.delivered_at.is_(None),
            )
        )
        or 0
    )


async def delete_department(session: AsyncSession, name: str) -> None:
    """Remove a department, refusing while it still owes deliveries.

    Deleting is blocked rather than cascaded: those confessions were
    forwarded on the promise that somebody would receive them, and dropping
    the row would strand them silently. Deactivate instead — that stops new
    forwards without discarding the queue.

    Raises:
        DepartmentNotFoundError: If no department is called *name*.
        DepartmentInUseError: If undelivered confessions still target it.
    """
    department = await get_by_name(session, name)
    if department is None:
        raise DepartmentNotFoundError(f"no department called {name}")

    pending = await undelivered_count(session, name)
    if pending:
        raise DepartmentInUseError(
            f"{name} still has {pending} undelivered confession(s); "
            "deactivate it instead of deleting it"
        )

    await session.delete(department)
    await session.flush()


async def resolve_chat_id(session: AsyncSession, name: str) -> str | None:
    """Return the Telegram chat configured for *name*, if any."""
    department = await get_by_name(session, name)
    return department.telegram_chat_id if department else None
