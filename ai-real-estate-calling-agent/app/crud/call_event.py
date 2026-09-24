"""CRUD operations for CallEvent (append-only lifecycle events)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_event import CallEvent


async def add_event(
    db: AsyncSession,
    *,
    call_id: uuid.UUID,
    event_type: str,
    data: dict | str | None = None,
    occurred_at: datetime | None = None,
) -> CallEvent:
    """Append an event to a call (no commit - caller controls transaction)."""
    if isinstance(data, (dict, list)):
        import json

        data = json.dumps(data)
    event = CallEvent(
        call_id=str(call_id),
        event_type=event_type,
        data=data,
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )
    db.add(event)
    await db.flush()
    return event


async def list_events_for_call(db: AsyncSession, call_id: uuid.UUID) -> list[CallEvent]:
    stmt = (
        select(CallEvent)
        .where(CallEvent.call_id == str(call_id))
        .order_by(CallEvent.occurred_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())
