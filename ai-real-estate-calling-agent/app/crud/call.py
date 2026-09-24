"""CRUD operations for the Call resource."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.schemas.call import CallCreate, CallUpdate


async def create_call(db: AsyncSession, data: CallCreate) -> Call:
    call = Call(**data.model_dump())
    db.add(call)
    await db.flush()
    await db.refresh(call)
    return call


async def get_call(db: AsyncSession, call_id: uuid.UUID) -> Call | None:
    stmt = select(Call).where(Call.id == call_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_call_by_provider_id(db: AsyncSession, provider_call_id: str) -> Call | None:
    stmt = select(Call).where(Call.provider_call_id == provider_call_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_calls(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    lead_id: uuid.UUID | None = None,
) -> tuple[list[Call], int]:
    filters = []
    if status:
        filters.append(Call.status == status)
    if lead_id is not None:
        filters.append(Call.lead_id == lead_id)

    count_stmt = select(func.count()).select_from(Call).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Call)
        .where(*filters)
        .order_by(Call.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def update_call(db: AsyncSession, call: Call, data: CallUpdate) -> Call:
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(call, field, value)
    await db.flush()
    await db.refresh(call)
    return call


async def set_call_status(db: AsyncSession, call: Call, status: str) -> Call:
    call.status = status
    await db.flush()
    await db.refresh(call)
    return call
