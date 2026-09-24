"""CRUD operations for the CostRecord resource (Phase 15)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_record import CostRecord
from app.schemas.cost import CostRecordCreate


async def create_cost_record(db: AsyncSession, data: CostRecordCreate) -> CostRecord:
    record = CostRecord(**data.model_dump())
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def get_cost_records_for_call(
    db: AsyncSession, call_id: uuid.UUID
) -> list[CostRecord]:
    stmt = (
        select(CostRecord)
        .where(CostRecord.call_id == call_id)
        .order_by(CostRecord.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_cost_total_for_call(
    db: AsyncSession, call_id: uuid.UUID
) -> float:
    stmt = select(func.coalesce(func.sum(CostRecord.amount), 0.0)).where(
        CostRecord.call_id == call_id
    )
    return float((await db.execute(stmt)).scalar_one())
