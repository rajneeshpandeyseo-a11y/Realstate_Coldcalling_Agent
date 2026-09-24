"""CRUD operations for the FollowUp resource (Phase 14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import FollowUpStatus
from app.models.follow_up import FollowUp
from app.schemas.follow_up import FollowUpCreate, FollowUpUpdate


async def create_follow_up(db: AsyncSession, data: FollowUpCreate) -> FollowUp:
    follow_up = FollowUp(**data.model_dump())
    db.add(follow_up)
    await db.flush()
    await db.refresh(follow_up)
    return follow_up


async def get_follow_up(db: AsyncSession, follow_up_id: uuid.UUID) -> FollowUp | None:
    stmt = select(FollowUp).where(FollowUp.id == follow_up_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_follow_ups_for_lead(
    db: AsyncSession, lead_id: uuid.UUID
) -> list[FollowUp]:
    stmt = (
        select(FollowUp)
        .where(FollowUp.lead_id == lead_id)
        .order_by(FollowUp.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_follow_ups(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    lead_id: uuid.UUID | None = None,
) -> tuple[list[FollowUp], int]:
    filters = []
    if status:
        filters.append(FollowUp.status == status)
    if lead_id is not None:
        filters.append(FollowUp.lead_id == lead_id)

    count_stmt = select(func.count()).select_from(FollowUp).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(FollowUp)
        .where(*filters)
        .order_by(FollowUp.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def list_due(
    db: AsyncSession, *, now: datetime, limit: int = 100
) -> list[FollowUp]:
    """Follow-ups pending and due for processing (retry worker)."""
    stmt = (
        select(FollowUp)
        .where(
            FollowUp.status == FollowUpStatus.PENDING.value,
            FollowUp.scheduled_for.is_not(None),
            FollowUp.scheduled_for <= now,
            FollowUp.attempts < FollowUp.max_attempts,
        )
        .order_by(FollowUp.scheduled_for.asc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def update_follow_up(
    db: AsyncSession, follow_up: FollowUp, data: FollowUpUpdate
) -> FollowUp:
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(follow_up, field, value)
    await db.flush()
    await db.refresh(follow_up)
    return follow_up


async def set_follow_up_status(
    db: AsyncSession, follow_up: FollowUp, status: str
) -> FollowUp:
    follow_up.status = status
    await db.flush()
    await db.refresh(follow_up)
    return follow_up
