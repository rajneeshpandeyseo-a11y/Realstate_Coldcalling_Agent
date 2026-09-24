"""CRUD operations for the SiteVisit resource (Phase 13)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.site_visit import SiteVisit
from app.schemas.site_visit import SiteVisitCreate, SiteVisitUpdate


async def create_site_visit(db: AsyncSession, data: SiteVisitCreate) -> SiteVisit:
    visit = SiteVisit(**data.model_dump())
    db.add(visit)
    await db.flush()
    await db.refresh(visit)
    return visit


async def get_site_visit(db: AsyncSession, visit_id: uuid.UUID) -> SiteVisit | None:
    stmt = select(SiteVisit).where(SiteVisit.id == visit_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_site_visits_for_lead(
    db: AsyncSession, lead_id: uuid.UUID
) -> list[SiteVisit]:
    stmt = (
        select(SiteVisit)
        .where(SiteVisit.lead_id == lead_id)
        .order_by(SiteVisit.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_site_visits(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    lead_id: uuid.UUID | None = None,
) -> tuple[list[SiteVisit], int]:
    filters = []
    if status:
        filters.append(SiteVisit.status == status)
    if lead_id is not None:
        filters.append(SiteVisit.lead_id == lead_id)

    count_stmt = select(func.count()).select_from(SiteVisit).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(SiteVisit)
        .where(*filters)
        .order_by(SiteVisit.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def update_site_visit(
    db: AsyncSession, visit: SiteVisit, data: SiteVisitUpdate
) -> SiteVisit:
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(visit, field, value)
    await db.flush()
    await db.refresh(visit)
    return visit


async def set_site_visit_status(
    db: AsyncSession, visit: SiteVisit, status: str
) -> SiteVisit:
    visit.status = status
    await db.flush()
    await db.refresh(visit)
    return visit
