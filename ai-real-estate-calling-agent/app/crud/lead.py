"""CRUD operations for the Lead resource."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.schemas.lead import LeadCreate, LeadUpdate


async def create_lead(db: AsyncSession, data: LeadCreate) -> Lead:
    """Create a new lead."""
    lead = Lead(**data.model_dump())
    db.add(lead)
    await db.flush()
    await db.refresh(lead)
    return lead


async def get_lead(db: AsyncSession, lead_id: uuid.UUID) -> Lead | None:
    """Fetch a single active lead by id."""
    stmt = select(Lead).where(Lead.id == lead_id, Lead.deleted_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_lead_by_phone(db: AsyncSession, phone: str) -> Lead | None:
    """Fetch a single active lead by phone number."""
    stmt = select(Lead).where(Lead.phone == phone, Lead.deleted_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_leads(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    search: str | None = None,
) -> tuple[list[Lead], int]:
    """List active leads with filtering/pagination.

    Returns (rows, total).
    """
    filters = [Lead.deleted_at.is_(None)]
    if status:
        filters.append(Lead.status == status)
    if search:
        like = f"%{search}%"
        filters.append(Lead.name.ilike(like) | Lead.phone.ilike(like))

    count_stmt = select(func.count()).select_from(Lead).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Lead)
        .where(*filters)
        .order_by(Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def update_lead(db: AsyncSession, lead: Lead, data: LeadUpdate) -> Lead:
    """Apply a partial update to an existing lead."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(lead, field, value)
    await db.flush()
    await db.refresh(lead)
    return lead


async def soft_delete_lead(db: AsyncSession, lead: Lead, reason: str | None = None) -> None:
    """Mark a lead as deleted rather than removing the row."""
    from datetime import datetime, timezone

    lead.deleted_at = datetime.now(timezone.utc)
    lead.delete_reason = reason or "manual delete"
    await db.flush()
